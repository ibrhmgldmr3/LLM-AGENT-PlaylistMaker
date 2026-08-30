"""Isi calistiran tarafin yapilandirmayi ORTAMDAN kurmasi.

Payload'da yapilandirma TASINMIYOR ve bu bilincli bir karar:

1. `AppConfig` API anahtarlarini tasiyor. Onlari kuyruga yazmak sirlari
   broker'a -- ve oradan diskine, yedeklerine, loglarina -- yaymak olurdu.
2. Kullaniciya OZGU bir sir zaten yok: `api/deps.get_user_credentials` bos bir
   `UserCredentials()` donduruyor ve butun anahtarlar `.env`'den geliyor.
   Yani ortamdan yeniden kurmak, bugunku davranisin BIREBIR aynisi -- bu
   modul davranis degil yalnizca KAYNAK degistiriyor.

Boylece isi calistiran taraf HTTP katmanini hic tanimadan calisabiliyor:
bugun ayni surecte, yarin ayri bir worker'da.
"""

from __future__ import annotations

from typing import Any

from src.config import AppConfig, UserCredentials, settings
from src.storage import SQLiteStore, create_store


def runtime_config(option_overrides: dict[str, Any] | None = None) -> AppConfig:
    """Ortamdaki yapilandirma + istege ozgu secenek ezmeleri.

    `api/deps.build_run_config` ile AYNI birlesimi kuruyor; fark yalnizca
    kaynaklarin FastAPI bagimliliklarindan degil dogrudan ortamdan gelmesi.
    """
    base = settings.base_config()
    options = base.run_options()
    if option_overrides:
        options = options.model_copy(update=option_overrides)
    config = AppConfig.compose(
        server=base.server_config(),
        credentials=UserCredentials(),
        options=options,
    )
    config.ensure_directories()
    return config


def runtime_store(config: AppConfig) -> SQLiteStore:
    return create_store(config)
