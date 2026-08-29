"""Yapilandirmaya gore is kosucusu secer.

Secim TEK YERDE: `api/main` ve worker giris noktasi ayni fonksiyonu cagiriyor,
boylece "web hangi backend'i kullaniyor, worker hangisini" diye ayrisabilecek
bir nokta kalmiyor.
"""

from __future__ import annotations

import logging

from src.config import AppConfig
from src.jobs.runner import InProcessJobRunner, JobRunner

_log = logging.getLogger(__name__)

# `InProcessJobRunner` icin es zamanli is sayisi. Bilerek dusuk: her is zaten
# kendi icinde arama/transkript icin thread havuzu aciyor ve YouTube hiz
# sinirlari sunucu IP'sine bagli.
DEFAULT_IN_PROCESS_WORKERS = 2


def create_job_runner(config: AppConfig) -> JobRunner:
    if config.job_backend == "redis":
        # `redis` ZORUNLU BAGIMLILIK DEGIL: yalnizca bu dalda import ediliyor,
        # boylece varsayilan (`memory`) kurulum paketi hic kurmadan calisiyor.
        import redis

        from src.jobs.redis_runner import RedisJobRunner

        client = redis.Redis.from_url(config.redis_url, decode_responses=True)
        _log.info("İş kosucusu: redis (%s)", config.redis_url)
        return RedisJobRunner(
            client, prefix=config.job_key_prefix, ttl_sec=config.job_ttl_sec
        )

    _log.info("İş kosucusu: bellek içi (tek süreç)")
    return InProcessJobRunner(max_workers=DEFAULT_IN_PROCESS_WORKERS)
