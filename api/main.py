"""FastAPI uygulamasi — uygulamanin tek giris noktasi.

`web/dist` derlenmisse arayuzu de buradan servis eder, yani uretimde tek surec
yeter. Gelistirmede Vite kendi sunucusunu calistirip `/api`'yi buraya proxy'ler.

Calistirma:
    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.deps import _base_config, get_server_config
from src.config.settings import DEV_CORS_ORIGINS
from api.routers import admin as admin_router
from api.routers import auth as auth_router
from api.routers import config as config_router
from api.routers import runs as runs_router
from api.routers import spaces as spaces_router
from src.jobs.factory import create_job_runner
from src.providers.errors import (
    ProviderPermanentError,
    ProviderRateLimitedError,
    ProviderTemporaryError,
)
from src.utils.logging_utils import redact_secrets

logger = logging.getLogger("api")

def _cors_origins() -> list[str]:
    """CORS kokenlerini yapilandirmadan okur; okunamazsa gelistirme kokenleri.

    Yapilandirma IMPORT ZAMANINDA okunuyor (middleware o an kuruluyor) ve
    `load_config` eksik `GEMINI_API_KEY` gibi durumlarda `RuntimeError`
    firlatiyor. Burada patlamak, uygulamanin HIC import edilememesi demek
    olurdu -- CI'nin "uygulama import edilebiliyor mu" adimi da tam olarak
    `.env`siz calisiyor. Yapilandirma hatasi ilk istekte zaten yuzeye cikiyor;
    dogru davranis guvenli varsayilana dusup devam etmek.
    """
    try:
        server = get_server_config()
    except Exception:
        return list(DEV_CORS_ORIGINS)

    if server.cors_wildcard_rejected():
        logger.warning(
            "CORS_ALLOW_ORIGINS içinde `*` yok sayıldı: bu API oturum çerezi "
            "taşıyor ve joker köken, herhangi bir sitenin kullanıcı adına "
            "istek atması anlamına gelirdi. Kökenleri açıkça listeleyin."
        )
    return server.cors_origins()


def _warn_on_unbounded_shared_quota() -> None:
    """Paylasimli anahtar + sinirsiz calistirma kombinasyonunu gorunur kilar.

    BYOK kaldirildiktan sonra TUM sorgular sunucunun KENDI anahtarlariyla
    gidiyor. YouTube Data API kotasi PROJE basina gunde 10.000 birim ve bir
    calistirma ~612-1200 birim tuketiyor -- yani ~8-16 calistirma TUM
    kullanicilar icin TOPLAM. `multi_user` modda sinir yoksa giris yapan tek
    bir kullanici gunu bitirebilir (ve LLM faturasini surebilir).

    HATA degil UYARI: kapali/davetli bir kurulumda sinirsiz birakmak mesru bir
    tercih. Sessiz kalmasi mesru degil -- varsayilan 0 oldugu icin bu durum
    yapilandirmaya hic dokunmayan kurulumlarda KENDILIGINDEN olusuyor.
    """
    try:
        server = get_server_config()
    except Exception as exc:
        # Yapilandirma hatasi ilk istekte zaten yuzeye cikiyor; acilisi
        # bir UYARI kontrolu yuzunden dusurmenin anlami yok.
        logger.warning("Yapılandırma açılışta okunamadı, kota kontrolü atlandı: %s", exc)
        return

    if server.auth_mode == "multi_user" and not server.max_runs_per_user_per_day:
        logger.warning(
            "AUTH_MODE=multi_user ama MAX_RUNS_PER_USER_PER_DAY=0 (sınırsız). "
            "LLM ve YouTube anahtarları PAYLAŞIMLI olduğu için giriş yapan tek bir "
            "kullanıcı günlük YouTube kotasının tamamını (~8-16 çalıştırma, tüm "
            "kullanıcılar toplamı) tüketebilir. .env.example bu mod için 3 öneriyor."
        )


def _log_config_warnings() -> None:
    """`.env` okunurken toplanan uyarilari gunluge yazar.

    `_collect_env_warnings` bunlari HESAPLIYOR ve `AppConfig.config_warnings`e
    koyuyordu, ama hicbir yerde okunmuyorlardi -- ne API yanitinda ne gunlukte.
    Yani "`.env`'e yazdim ama hicbir sey degismedi" durumunu tam da yakalamak
    icin var olan mekanizma, sonucunu sessizce cope atiyordu.
    """
    try:
        for warning in _base_config().config_warnings:
            logger.warning("%s", warning)
    except Exception as exc:
        # Yapilandirma hatasi ilk istekte zaten yuzeye cikiyor.
        logger.warning("Yapılandırma uyarıları okunamadı: %s", exc)


def _warn_on_plaintext_secrets() -> None:
    """Sifreleme anahtari yokken OAuth jetonlari DUZ METIN yaziliyor.

    `SECRET_ENCRYPTION_KEY` opsiyonel ve varsayilani yok, yani hicbir sey
    yapmayan bir kurulumda bu durum KENDILIGINDEN olusuyor -- tipki
    `_warn_on_unbounded_shared_quota`daki gibi. Jetonlar kullanicinin YouTube
    hesabinda oynatma listesi olusturma yetkisi tasiyor; veritabani dosyasini
    okuyan biri o yetkiyi dogrudan ele gecirir.

    HATA degil UYARI: tek kullanicili yerel bir kurulumda sifrelemesiz calismak
    mesru bir tercih. Sessiz kalmasi mesru degil.
    """
    try:
        server = get_server_config()
    except Exception:
        return
    if server.secret_encryption_key:
        return
    logger.warning(
        "SECRET_ENCRYPTION_KEY tanımlı değil: OAuth jetonları veritabanına DÜZ "
        "METİN yazılıyor. Üretimde tanımlayın (`python -m src.storage.crypto` "
        "ile üretebilirsiniz)."
    )


def _warn_on_multi_process() -> None:
    """Is durumu BELLEKTE; uygulama tek surec calismak zorunda.

    `InProcessJobRunner` tutamaclari, olay kanallarini ve future'lari surec
    icinde tutuyor. Ikinci bir surecte calisan istek, isi TANIMAYAN bir
    runner'a duser: SSE akisi ve `/status` "bilinmeyen calistirma" doner --
    is aslinda saglikli calisiyorken.

    Kesin tespit mumkun degil (uvicorn `--workers` icin standart bir isaret
    birakmiyor), ama gunicorn ve bazi PaaS'ler `WEB_CONCURRENCY` koyuyor.
    Onu gorursek acikca uyariyoruz; goremezsek de kisiti bir kez yaziyoruz ki
    kurulum yapan kisi bunu belgelerde aramak zorunda kalmasin.
    """
    # `redis` backend'inde is durumu PAYLASIMLI depoda; coklu surec/replika
    # tam olarak desteklenen kurulum ve burada uyarmak yaniltici olurdu.
    try:
        if get_server_config().job_backend == "redis":
            logger.info(
                "İş kosucusu: redis. Çoklu süreç/replika destekleniyor; işleri "
                "`python -m src.jobs.worker` ile ayrı bir süreçte çalıştırın."
            )
            return
    except Exception:
        pass

    concurrency = os.getenv("WEB_CONCURRENCY") or os.getenv("UVICORN_WORKERS")
    try:
        workers = int(concurrency) if concurrency else 1
    except ValueError:
        workers = 1
    if workers > 1:
        logger.error(
            "WEB_CONCURRENCY=%s: bu uygulama TEK SÜREÇ çalışmalı. İş durumu "
            "bellekte tutuluyor; ikinci süreçteki istek çalıştırmayı bulamaz ve "
            "ilerleme akışı kopar. Tek süreç çalıştırın.",
            workers,
        )
    else:
        logger.info("İş durumu bellekte tutuluyor: tek süreç çalıştırın (--workers kullanmayın).")


def _mark_interrupted_runs() -> None:
    """Onceki surecte yarida kalan calistirmalari isaretler.

    Acilista tanim geregi hicbir is calismiyor, dolayisiyla sonucu olmayan her
    satir gercekten yarida kalmis. Isaretlenenler gunluk hak sayimindan
    dusuluyor: sunucu yeniden basladi diye kullanici hicbir sey almadan hakkini
    kaybetmemeli. Gecmiste "yarim kaldi" olarak gorunmeye devam ediyorlar.
    """
    try:
        from src.storage import SQLiteStore

        server = get_server_config()
        store = SQLiteStore(server.sqlite_path, encryption_key=server.secret_encryption_key)
        marked = store.mark_interrupted_runs()
        if marked:
            logger.info("Yarıda kalan %s çalıştırma işaretlendi (günlük hak iade edildi)", marked)
    except Exception:
        logger.warning("Yarıda kalan çalıştırmalar işaretlenemedi", exc_info=True)


def _purge_orphan_run_dirs() -> None:
    """Veritabaninda karsiligi kalmayan calistirma dizinlerini toplar.

    ACILISTA calisiyor, her calistirmadan sonra degil: silme artik diski de
    temizledigi icin (bkz. `run_retention.delete_run`) sahipsiz dizin yalnizca
    cokme/yarida kalma sonucu olusuyor. Dizin taramasini her calistirmaya
    yaymak, cozdugu sorunla orantisiz bir maliyet olurdu.
    """
    try:
        from src.services.run_retention import purge_orphan_run_dirs
        from src.storage import SQLiteStore

        server = get_server_config()
        store = SQLiteStore(server.sqlite_path, encryption_key=server.secret_encryption_key)
        removed = purge_orphan_run_dirs(_base_config(), store)
        if removed:
            logger.info("Sahipsiz %s çalıştırma dizini silindi", removed)
    except Exception:
        # Bakim adimi acilisi DUSURMEMELI.
        logger.warning("Sahipsiz çalıştırma dizinleri temizlenemedi", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _log_config_warnings()
    _warn_on_multi_process()
    _warn_on_plaintext_secrets()
    _warn_on_unbounded_shared_quota()
    _mark_interrupted_runs()
    _purge_orphan_run_dirs()
    # Backend `JOB_BACKEND` ile seciliyor (varsayilan: bellek ici, tek surec).
    # Secim `create_job_runner` icinde TEK YERDE; worker giris noktasi da ayni
    # fonksiyonu cagiriyor, boylece web ile worker farkli backend kullanamiyor.
    app.state.job_runner = create_job_runner(_base_config())
    try:
        yield
    finally:
        app.state.job_runner.shutdown(wait=False)


app = FastAPI(
    title="Make A Playlist API",
    version="0.1.0",
    summary="Konudan YouTube öğrenme playlist'i üreten servisin HTTP arayüzü",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runs_router.router)
app.include_router(config_router.router)
app.include_router(auth_router.router)
app.include_router(auth_router.session_router)
app.include_router(admin_router.router)
app.include_router(spaces_router.router)


@app.get("/api/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


def _mount_frontend() -> None:
    """Derlenmis React uygulamasini ayni sunucudan servis eder.

    Uretimde tek surec yeter: `npm run build` sonrasi `uvicorn api.main:app`
    hem API'yi hem arayuzu servis eder. Gelistirmede `web/dist` bulunmaz ve
    Vite kendi sunucusundan servis edip `/api`'yi buraya proxy'ler.

    Rotalardan SONRA baglanir: `/api/*` ve `/docs` her zaman oncelikli.
    """
    dist = Path(__file__).resolve().parent.parent / "web" / "dist"
    if not (dist / "index.html").exists():
        logger.info("web/dist yok; arayüz Vite'tan servis ediliyor (geliştirme modu)")
        return
    app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    logger.info("Frontend mounted from %s", dist)


_mount_frontend()


# --------------------------------------------------------------- hata isleme
# Saglayici hatalari HTTP durumlarina eslenir; mesajlar maskelenir cunku
# YouTube API anahtari sorgu parametresi olarak gidiyor ve istisna metnine
# sizabiliyor.


@app.exception_handler(ProviderRateLimitedError)
async def handle_rate_limited(request: Request, exc: ProviderRateLimitedError) -> JSONResponse:
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": redact_secrets(str(exc)), "code": "rate_limited"},
        headers=headers,
    )


@app.exception_handler(ProviderPermanentError)
async def handle_permanent(request: Request, exc: ProviderPermanentError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": redact_secrets(str(exc)), "code": "provider_permanent"},
    )


@app.exception_handler(ProviderTemporaryError)
async def handle_temporary(request: Request, exc: ProviderTemporaryError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": redact_secrets(str(exc)), "code": "provider_temporary"},
    )


@app.exception_handler(RuntimeError)
async def handle_runtime(request: Request, exc: RuntimeError) -> JSONResponse:
    """Yapilandirma hatalari (`load_config`) RuntimeError olarak geliyor."""
    logger.warning("Runtime error on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": redact_secrets(str(exc)), "code": "runtime_error"},
    )
