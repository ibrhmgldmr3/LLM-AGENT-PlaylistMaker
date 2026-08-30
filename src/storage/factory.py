"""Deponun TEK kurulum yolu.

Depoyu kuran alti ayri cagri yeri vardi ve her biri lehce secimini kendi
basina yapmak zorunda kalacakti. Bir tanesinin unutulmasi, uygulamanin
YARISININ Postgres'e digger yarisinin SQLite'a yazmasi demekti -- sessizce
bolunmus bir veritabani, en gec fark edilen ariza turlerinden biri.

Secimi yapan tek sey `DATABASE_URL`:

    tanimli degil  -> SQLite (`sqlite_path`), bugunku davranis
    tanimli        -> Postgres (`src/storage/dialect.PostgresDialect`)
"""

from __future__ import annotations

from src.storage.dialect import Dialect, PostgresDialect
from src.storage.sqlite_store import SQLiteStore


def create_dialect(server) -> Dialect | None:
    """Yapilandirmanin istedigi lehce; SQLite icin `None` (store varsayilani)."""
    dsn = (getattr(server, "database_url", None) or "").strip()
    return PostgresDialect(dsn) if dsn else None


def create_store(server) -> SQLiteStore:
    """`ServerConfig` (ya da `AppConfig`) verilen depoyu kurar.

    Sifreleme anahtari HER ZAMAN veriliyor. Onceden bir cagri yeri
    (`playlist_service`) anahtarsiz kuruyordu; olculdu, o yol hicbir sir
    metoduna dokunmuyor, yani davranis degismiyor -- ama iki farkli kurulum
    bicimini korumak, aralarindaki farkin bir gun ANLAMLI hale gelmesini
    bekleyen bir tuzak olurdu.
    """
    return SQLiteStore(
        server.sqlite_path,
        encryption_key=server.secret_encryption_key,
        dialect=create_dialect(server),
    )


def backend_name(server) -> str:
    """Gunluge yazmak icin okunabilir depo adi."""
    return "postgres" if create_dialect(server) is not None else "sqlite"
