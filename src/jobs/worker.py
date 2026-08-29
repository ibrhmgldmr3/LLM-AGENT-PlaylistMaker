"""Isi CALISTIRAN surec.

Web tarafi isi kabul edip kuyruga koyuyor (`RedisJobRunner.submit`); burasi
kuyruktan alip calistiriyor. Ayirmanin somut kazanci: ASR gibi islemci yogun
isler web sureclerinin CPU'sunu yemiyor ve worker sayisi web sayisindan
BAGIMSIZ olcekleniyor.

Calistirma:

    python -m src.jobs.worker

Is turleri import EDILEREK kaydediliyor -- worker hangi isleri
calistirabilecegini bu import'lardan biliyor. Web tarafi da ayni modulleri
import ediyor, dolayisiyla iki taraf ayni adlari coziyor.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from typing import Any

from src.config import settings
from src.jobs import playlist_task, space_tasks
from src.jobs.redis_runner import RedisJobRunner
from src.jobs.runner import JobCancelled, JobState
from src.jobs.tasks import TaskContext, UnknownTask, resolve_task
from src.models import ProgressEvent
from src.utils.logging_utils import redact_secrets

_log = logging.getLogger("worker")

# Is turlerini KAYIT DEFTERINE yukleyen moduller. Import edilmeleri yeterli --
# kayit `register_task` cagrilariyla import aninda olusuyor. Demet halinde
# tutuluyorlar ki "neden bu import'lar duruyor" sorusu kodun kendisinde
# yanitlansin; biri silinirse worker o isi "bilinmeyen" sayip her zarfi atar.
TASK_MODULES = (playlist_task, space_tasks)


def build_runner() -> RedisJobRunner:
    """Yapilandirmadan bir Redis kosucusu kurar."""
    import redis

    config = settings.base_config()
    client = redis.Redis.from_url(config.redis_url, decode_responses=True)
    return RedisJobRunner(client, prefix=config.job_key_prefix, ttl_sec=config.job_ttl_sec)


def run_one(runner: RedisJobRunner, envelope: dict[str, Any]) -> None:
    """Tek bir zarfi calistirir. HICBIR ZAMAN istisna sizdirmaz.

    Sizdirsaydi tek bir bozuk is butun worker dongusunu durdururdu; kuyrukta
    bekleyen diger isler de islenmezdi.
    """
    job_id = envelope.get("job_id") or ""
    user_id = envelope.get("user_id") or ""
    task_name = envelope.get("task") or ""

    if not job_id:
        _log.warning("Kimliksiz is zarfi atlandi")
        return

    # Tutamaci ARTIK YOK: TTL'i dolmus, yani bu zarf kuyrukta `JOB_TTL_SEC`
    # boyunca beklemis (pratikte worker o kadar sure ayakta degildi).
    #
    # Calistirmamak GEREKIYOR. Isi bekleyen kimse kalmadi -- istemci coktan
    # "bilinmeyen calistirma" gordu -- ama is yine de YouTube kotasi ve LLM
    # cagrisi harcardi. Ustelik `mark_running`in `hset`i var olmayan hash'i
    # YENIDEN YARATIYOR: ortaya `user_id`si BOS bir tutamac cikiyor, yani
    # `_owned_handle` hicbir kullaniciyla eslesmiyor ve olusan kayit ne
    # gorulebiliyor ne iptal edilebiliyor -- taze bir TTL'le duran bir zombi.
    #
    # (Gercek Redis'e karsi kosarken bulundu; test ikizi anahtar suresi
    # dolmasini modellemedigi icin bunu gosteremezdi.)
    if runner.get(job_id) is None:
        _log.warning("Is %s tutamaci yok (TTL dolmus); zarf atiliyor", job_id)
        return

    # Kuyruktayken iptal edilmis olabilir: calistirmaya hic baslamiyoruz.
    if runner.is_cancelled(job_id):
        _log.info("Is %s kuyruktayken iptal edilmis", job_id)
        runner.finish(job_id, JobState.CANCELLED)
        return

    try:
        task_fn = resolve_task(task_name)
    except UnknownTask:
        # Worker bu is turunu tanimiyor. Kuyruga geri koymuyoruz: ayni zarf
        # sonsuza kadar dolasip her worker'da ayni sekilde basarisiz olurdu.
        _log.error("Bilinmeyen is turu: %s (is %s)", task_name, job_id)
        runner.finish(job_id, JobState.FAILED, error=f"Bilinmeyen iş türü: {task_name}")
        return

    context = TaskContext(job_id=job_id, user_id=user_id, payload=envelope.get("payload") or {})
    runner.mark_running(job_id)

    def emit(event: ProgressEvent) -> None:
        # Iptal ISBIRLIKCI: calisan is bunu bir sonraki ilerleme bildiriminde
        # ogreniyor. `InProcessJobRunner` ile ayni sozlesme; tek fark bayragin
        # bellekte degil paylasimli depoda olmasi.
        if runner.is_cancelled(job_id):
            raise JobCancelled(job_id)
        runner.append_event(job_id, event)

    try:
        task_fn(context, emit)
    except JobCancelled:
        _log.info("Is %s iptal edildi", job_id)
        runner.finish(job_id, JobState.CANCELLED)
    except BaseException as exc:
        # MASKELENEREK saklaniyor: bu alan `/status` yanitina ve SSE `done`
        # olayina girip DOGRUDAN istemciye gidiyor.
        message = redact_secrets(str(exc))
        # Yigin izi SUNUCUDA kaliyor; istemciye giden yalnizca ozet.
        _log.exception("Is %s basarisiz", job_id)
        runner.finish(job_id, JobState.FAILED, error=message)
    else:
        runner.finish(job_id, JobState.DONE)


def serve(runner: RedisJobRunner | None = None, stop: threading.Event | None = None) -> None:
    """Kuyrugu dinler. `stop` isaretlenene kadar donmez.

    `stop` disaridan verilebiliyor ki testler dongunun bir turunu calistirip
    cikabilsin -- sinyal kurulumuna bagimli olmayan bir giris noktasi.
    """
    runner = runner or build_runner()
    stop = stop or threading.Event()
    _log.info("Worker basladi; kuyruk: %s", runner.queue_key)

    while not stop.is_set():
        try:
            envelope = runner.claim(timeout=5.0)
        except Exception:
            # Redis gecici olarak erisilemez olabilir. Dongu OLMEMELI:
            # worker'in yeniden baslatilmasi gerekseydi kisa bir ag
            # kesintisi butun arka plan islemesini durdururdu.
            _log.warning("Kuyruk okunamadi; yeniden denenecek", exc_info=True)
            stop.wait(2.0)
            continue
        if envelope is None:
            continue
        run_one(runner, envelope)

    _log.info("Worker durduruldu")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    stop = threading.Event()

    def _request_stop(signum, _frame):
        # Calisan is YARIDA KESILMIYOR: bayrak yalnizca "yeni is alma" diyor.
        # Isi ortasindan kesmek, kota rezervasyonunu ve kismi yazimlari
        # belirsiz birakirdi.
        _log.info("Sinyal %s alindi; suren is bitince cikilacak", signum)
        stop.set()

    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", signal.SIGINT)):
        try:
            signal.signal(sig, _request_stop)
        except (ValueError, OSError):
            # Ana thread disinda ya da desteklenmeyen platformda.
            pass

    serve(stop=stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
