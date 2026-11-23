"""FastAPI bagimliliklari.

Kimlik BURADA cozuluyor; rotalarin hicbiri oturum mekanigini bilmiyor.
`ServerConfig.auth_mode` iki kurulumu ayiriyor:

- `single_user` (varsayilan): her istek `DEFAULT_USER_ID`'ye ait, oturum
  cerezi yok. Tek kisilik kurulum ve gelistirme akisi.
- `multi_user`: istek bir oturum cerezi (Google girisi) tasimali.

LLM ve YouTube arama anahtarlari PAYLASIMLI: her iki modda da `.env`'den
gelir, kullanici hicbir anahtar girmiyor (bkz. `get_user_credentials`).
`multi_user`'in tek etkisi kimligi bilmek -- gunluk sinir ve kisisel
YouTube yayinlama izni (OAuth) icin.

Vaat tutuldu: cok kullanicili moda gecerken rota imzalari hic degismedi.
"""

from __future__ import annotations

import ipaddress
import logging

from fastapi import Depends, HTTPException, Request, status

from src.config import AppConfig, RunOptions, ServerConfig, UserCredentials, settings
from src.jobs import JobRunner
from src.storage import DEFAULT_USER_ID, SQLiteStore, create_store


def _base_config() -> AppConfig:
    """Taban yapilandirma. Onbellek `src.config.settings` icinde.

    Erisim MODUL UZERINDEN: isi calistiran taraf (`src/jobs/runtime`) da ayni
    fonksiyonu cagiriyor ve tek yama noktasi olmasi bunu gerektiriyor. Kendi
    `lru_cache`i vardi; iki ayri onbellek, iki tarafin FARKLI yapilandirma
    okumasi demekti.
    """
    return settings.base_config()


def get_server_config() -> ServerConfig:
    return _base_config().server_config()


SESSION_COOKIE = "map_session"

_log = logging.getLogger("api")

# Surec basina BIR KEZ: her istekte yazmak gunlugu bogar ve uyariyi okunmaz
# kilar. Testler sifirlayabilsin diye modul duzeyinde.
_exposure_warned = False


def _warn_if_single_user_is_exposed(request: Request) -> None:
    """`single_user` sunucusuna YEREL OLMAYAN bir istemci ulastiysa uyarir.

    Bu modda oturum yok: istegi yapan HERKES sunucu sahibi (`local`) sayiliyor
    -- gecmisi silebilir, sahibin YouTube hesabina liste yayinlayabilir ve
    kullanim raporunu okuyabilir. Varsayilan Docker kurulumu tam olarak bu:
    `AUTH_MODE` tanimsiz ve CMD `0.0.0.0`a baglaniyor.

    Uygulama hangi adrese BAGLANDIGINI guvenilir bicimde bilemiyor (uvicorn
    bunu uygulamaya vermiyor). Asil soruyu yanitlayan sinyal daha iyi: yerel
    olmayan bir istemci GERCEKTEN ulasti mi. Ayni makinedeki bir ters proxy
    arkasinda uvicorn `X-Forwarded-For`u 127.0.0.1'den guvenilir saydigi icin
    istemci adresi gercek adres oluyor ve uyari yine calisiyor.

    IP olmayan istemci (test istemcisi, unix soketi) icin karar verilemiyor;
    sessiz geciliyor -- yanlis alarm, gercek alarmi da okunmaz kilardi.
    """
    global _exposure_warned
    if _exposure_warned:
        return
    host = request.client.host if request.client else None
    if not host:
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    # `::ffff:127.0.0.1` gibi IPv4-eslemeli adresler: 3.13 oncesinde
    # `is_loopback` bunlari tanimiyor.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if address.is_loopback:
        return
    _exposure_warned = True
    _log.warning(
        "AUTH_MODE=single_user ama istek yerel olmayan bir adresten geldi (%s). "
        "Bu modda oturum yok ve isteği yapan HERKES sunucu sahibi sayılır: "
        "geçmişi silebilir, sahibin YouTube hesabına liste yayınlayabilir ve "
        "kullanım raporunu okuyabilir. Sunucu başkalarına açıksa "
        "AUTH_MODE=multi_user kullanın.",
        host,
    )


def get_current_user_optional(request: Request) -> str | None:
    """Istegin sahibi; oturum yoksa `None`. 401 FIRLATMAZ.

    "Sunucuda ne yapilandirilmis" sorusunu yanitlayan uclar (`/api/config`)
    icin: o uclarin oturuma bagimli olmasi tavuk-yumurta hatasi yaratiyor --
    arayuz daha giris ekranini cizerken yetenekleri soruyor ve 401 yiyince
    hepsini SESSIZCE kaybediyor. Kullaniciya OZGU alanlar (kalan calistirma
    hakki) oturum yokken bos birakilir; geri kalan yanit yine dolu doner.

    `single_user` modunda (varsayilan) her istek `DEFAULT_USER_ID`'ye ait --
    tek kisilik kurulum ve gelistirme akisi boyle calisiyor.
    """
    server = _base_config().server_config()
    if server.auth_mode == "single_user":
        _warn_if_single_user_is_exposed(request)
        return DEFAULT_USER_ID

    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    store = create_store(server)
    session = store.get_session(token)
    return session["user_id"] if session else None


def get_current_user(user_id: str | None = Depends(get_current_user_optional)) -> str:
    """Istegin sahibi olan kullanici; oturum sartsa YOKSA 401.

    Kimligi yalnizca burada cozmek bilincli: rotalarin hicbiri oturum
    mekanigini bilmiyor, `Depends(get_current_user)` yeterli.
    """
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Oturum açmanız gerekiyor")
    return user_id


def get_user_credentials(server: ServerConfig = Depends(get_server_config)) -> UserCredentials:
    """Kullanicinin API anahtarlari.

    ARTIK PAYLASIMLI: LLM (Gemini/Together/OpenRouter) ve YouTube Data API
    anahtarlari her iki auth modunda da `ServerConfig` uzerinden `.env`'den
    geliyor -- kullanici hicbir anahtar girmiyor (bkz. `src/config/settings.py`
    ust docstring'i). Bu fonksiyon yalnizca sunucunun gercekten
    yapilandirilmis oldugunu dogruluyor; degilse istek SESSIZCE degil ACIKCA
    503 aliyor.

    Bakilan anahtar SECILEN saglayicininki (`llm_api_key()`). Onceden
    saglayicilar elle sayiliyordu (`together or gemini`): OpenRouter listede
    yoktu, ve secili olmayan bir saglayicinin anahtari kontrolu geciriyor,
    is ilk LLM cagrisinda dusuyordu -- kota rezerve edildikten SONRA.
    """
    if not server.llm_api_key():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Sunucu için LLM sağlayıcı anahtarı yapılandırılmamış: "
            f"LLM_PROVIDER={server.llm_provider} için {server.llm_api_key_env()} tanımlı değil",
        )
    return UserCredentials()


def require_admin(
    user_id: str = Depends(get_current_user),
    server: ServerConfig = Depends(get_server_config),
) -> str:
    """Kullanim raporunu gorebilecek kullaniciyi dogrular.

    `single_user` modda tek kullanici zaten sunucunun sahibi -- ayrica bir
    yetki kavrami uydurmanin anlami yok.

    `multi_user` modda `ADMIN_USER_IDS` listesi belirleyici ve BOSKEN HERKESE
    KAPALI. Ters varsayilan (liste bossa herkese acik) yapilandirmayi unutan
    kurulumlarda kullanici kimliklerini ve kullanim aliskanligini herkese
    gosterirdi; bu, sessizce olusan turden bir sizinti olurdu.
    """
    if server.auth_mode == "single_user":
        return user_id
    if user_id in server.admin_ids():
        return user_id
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu sayfayı görme yetkiniz yok")


def get_default_run_options() -> RunOptions:
    """`.env`'deki varsayilanlar; istek govdesi bunlari ezebilir."""
    return _base_config().run_options()


def get_store(server: ServerConfig = Depends(get_server_config)) -> SQLiteStore:
    return create_store(server)


def get_job_runner(request: Request) -> JobRunner:
    return request.app.state.job_runner


def build_run_config(
    credentials: UserCredentials,
    options: RunOptions,
    server: ServerConfig,
) -> AppConfig:
    """Bir calistirma icin uc kaynagi birlestirir."""
    config = AppConfig.compose(server=server, credentials=credentials, options=options)
    config.ensure_directories()
    return config
