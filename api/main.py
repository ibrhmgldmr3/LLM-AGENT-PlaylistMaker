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

from api.deps import get_server_config
from api.routers import auth as auth_router
from api.routers import config as config_router
from api.routers import runs as runs_router
from src.jobs import InProcessJobRunner
from src.providers.errors import (
    ProviderPermanentError,
    ProviderRateLimitedError,
    ProviderTemporaryError,
)
from src.utils.logging_utils import redact_secrets

logger = logging.getLogger("api")

# Vite gelistirme sunucusu. Uretimde ayni kokenden servis edilecegi icin
# CORS'a gerek kalmaz; simdilik yerel gelistirme icin acik.
DEV_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.name == "nt":
        os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    _warn_on_unbounded_shared_quota()
    # Es zamanli calistirma sayisi bilerek dusuk: her is zaten kendi icinde
    # arama/transkript icin thread havuzu aciyor ve YouTube hiz sinirlari
    # sunucu IP'sine bagli.
    app.state.job_runner = InProcessJobRunner(max_workers=2)
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
    allow_origins=DEV_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runs_router.router)
app.include_router(config_router.router)
app.include_router(auth_router.router)
app.include_router(auth_router.session_router)


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
