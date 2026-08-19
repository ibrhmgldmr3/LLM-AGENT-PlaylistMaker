"""FastAPI bagimliliklari.

Kimlik ve kullanici anahtarlari BURADA cozuluyor; rotalarin hicbiri oturum
mekanigini ya da anahtarlarin nereden geldigini bilmiyor. `ServerConfig.auth_mode`
iki kurulumu ayiriyor:

- `single_user` (varsayilan): her istek `DEFAULT_USER_ID`'ye ait, anahtarlar
  `.env`'den. Tek kisilik kurulum ve gelistirme akisi.
- `multi_user`: istek bir oturum cerezi tasimali, anahtarlar kullanici basina
  sifreli olarak veritabanindan okunur.

Vaat tutuldu: cok kullanicili moda gecerken rota imzalari hic degismedi.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, HTTPException, Request, status

from src.config import AppConfig, RunOptions, ServerConfig, UserCredentials, load_config
from src.jobs import JobRunner
from src.storage import DEFAULT_USER_ID, SQLiteStore


@lru_cache(maxsize=1)
def _base_config() -> AppConfig:
    """`.env`'den okunan taban yapilandirma. Surec omru boyunca bir kez."""
    return load_config()


def get_server_config() -> ServerConfig:
    return _base_config().server_config()


SESSION_COOKIE = "map_session"


def get_current_user(request: Request) -> str:
    """Istegin sahibi olan kullanici.

    `single_user` modunda (varsayilan) her istek `DEFAULT_USER_ID`'ye ait --
    tek kisilik kurulum ve gelistirme akisi boyle calisiyor.

    `multi_user` modunda istek gecerli bir oturum cerezi tasimali. Cerezdeki
    jeton veritabaninda OZETIYLE aranir; kayit yoksa ya da suresi gecmisse
    401 doner.

    Kimligi yalnizca burada cozmek bilincli: rotalarin hicbiri oturum
    mekanigini bilmiyor, `Depends(get_current_user)` yeterli.
    """
    server = _base_config().server_config()
    if server.auth_mode == "single_user":
        return DEFAULT_USER_ID

    token = request.cookies.get(SESSION_COOKIE)
    if token:
        store = SQLiteStore(server.sqlite_path, encryption_key=server.secret_encryption_key)
        session = store.get_session(token)
        if session:
            return session["user_id"]

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Oturum açmanız gerekiyor")


def get_user_credentials(
    user_id: str = Depends(get_current_user),
    server: ServerConfig = Depends(get_server_config),
) -> UserCredentials:
    """Kullanicinin API anahtarlari.

    `single_user` modunda `.env`'den geliyor.

    `multi_user` modunda YALNIZCA kullanicinin kendi kayitli anahtarlari
    kullaniliyor; `.env` degerleri yedege DUSMUYOR. Duseydi, anahtarini
    girmemis bir kullanici sessizce kurulum sahibinin YouTube kotasini
    harcardi -- kota proje basina gunde 10.000 birim ve bir calistirma ~1.200
    birim tuketiyor, yani ikinci kullanici gunu bitirirdi.

    Anahtari olmayan kullanici 400 aliyor: sessizce baskasinin kotasindan
    harcamaktansa acik bir hata daha dogru.
    """
    if server.auth_mode == "single_user":
        return _base_config().credentials()

    store = SQLiteStore(server.sqlite_path, encryption_key=server.secret_encryption_key)
    stored = store.get_user_credentials(user_id)

    # Hangi saglayici kullanilacak, GIRILEN ANAHTARDAN cikariliyor: Together
    # anahtari varsa Together, yoksa Gemini. Ayri bir "saglayici sec" ayari
    # koymadik cunku ikisi de girilmediginde secim anlamsiz, biri girildiginde
    # ise zaten belli. Ikisi de varsa Together kazaniyor -- kullanici sonradan
    # ekledigi anahtarla calismak ister; Gemini'ye donmek icin onu siliyor.
    provider = "together" if stored.get("together_api_key") else "gemini"
    if not (stored.get("together_api_key") or stored.get("gemini_api_key")):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Gemini veya Together.ai API anahtarınızı ayarlardan girmeniz gerekiyor",
        )
    return UserCredentials(llm_provider=provider, **stored)


def get_default_run_options() -> RunOptions:
    """`.env`'deki varsayilanlar; istek govdesi bunlari ezebilir."""
    return _base_config().run_options()


def get_store(server: ServerConfig = Depends(get_server_config)) -> SQLiteStore:
    return SQLiteStore(server.sqlite_path, encryption_key=server.secret_encryption_key)


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
