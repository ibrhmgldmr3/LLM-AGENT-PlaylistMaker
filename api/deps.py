"""FastAPI bagimliliklari.

Cok kullanicili moda gecisin ana kaldiraci burasi: `get_current_user` bugun
sabit bir deger donuyor, yarin gercek kimlik dogrulamayla degistirilecek.
Imzasi degismeyecegi icin rota kodu ayni kalir.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, Request

from src.config import AppConfig, RunOptions, ServerConfig, UserCredentials, load_config
from src.jobs import JobRunner
from src.storage import DEFAULT_USER_ID, SQLiteStore


@lru_cache(maxsize=1)
def _base_config() -> AppConfig:
    """`.env`'den okunan taban yapilandirma. Surec omru boyunca bir kez."""
    return load_config()


def get_server_config() -> ServerConfig:
    return _base_config().server_config()


def get_current_user(request: Request) -> str:
    """Istegin sahibi olan kullanici.

    Su an tek kullanicili: her istek `DEFAULT_USER_ID`'ye ait. Gercek kimlik
    dogrulama eklendiginde YALNIZCA bu fonksiyonun govdesi degisir — rotalar
    `Depends(get_current_user)` kullandigi icin hicbiri elden gecirilmez.

    `request` bilerek imzada: gercek bir uygulamanin `Authorization` basligina
    ya da oturum cerezine ihtiyaci olacak ve o gun imza degistirmek gerekmesin.
    """
    del request  # tek kullanicili kurulumda gerekmiyor
    return DEFAULT_USER_ID


def get_user_credentials(user_id: str = Depends(get_current_user)) -> UserCredentials:
    """Kullanicinin API anahtarlari.

    Bugun `.env`'den geliyor (tek kullanici). BYOK'a gecince kullanici basina
    sifreli olarak veritabanindan okunacak; imza ayni kalir.
    """
    return _base_config().credentials()


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
