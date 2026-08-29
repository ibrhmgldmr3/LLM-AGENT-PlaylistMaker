"""`JobRunner`in paylasimli depo kullanan uygulamasi.

`InProcessJobRunner` durumu bellekte tutuyor: tutamaclar, olay kanallari ve
future'lar surece ait. Bu, uygulamanin TEK SUREC calismasini zorunlu kiliyordu
-- ikinci bir surece dusen istek isi tanimiyor, SSE akisi kopuyor ve `/status`
"bilinmeyen calistirma" donuyordu. Buradaki uygulama ayni sozlesmeyi Redis
uzerinden karsiliyor, boylece isi KABUL EDEN surec ile CALISTIRAN surec ayri
olabiliyor.

Anahtar duzeni (hepsi TTL'li):

    {prefix}:job:{id}        HASH    tutamac alanlari (durum, ilerleme, hata)
    {prefix}:job:{id}:ev     LIST    olay gunlugu, JSON satirlari
    {prefix}:job:{id}:end    STRING  akis kapandi isareti
    {prefix}:job:{id}:cx     STRING  iptal ISTEGI
    {prefix}:queue           LIST    isi bekleyen zarflar (worker BLPOP'lar)
    {prefix}:job:{id}:ch     KANAL   yeni olay/kapanis bildirimi (pub/sub)

Olay gunlugu neden LIST, PUB/SUB DEGIL: pub/sub yalnizca O AN dinleyene
ulasir. Sozlesme ise "her abonenin kendi imleci var" ve "`Last-Event-ID` ile
kaldigi yerden devam" diyor -- yeniden baglanan bir tarayici kacirdigi
olaylari ISTEYEBILMELI. LIST indeksleri kalicidir ve `LRANGE` tam olarak bunu
verir. Pub/sub yalnizca UYANDIRMA icin kullaniliyor; veri her zaman LIST'ten
okunuyor.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Iterator

from src.jobs.runner import JobHandle, JobState
from src.jobs.tasks import resolve_task
from src.models import ProgressEvent

_log = logging.getLogger(__name__)

DEFAULT_PREFIX = "map"
# Bitmis islerin ne kadar okunabilir kalacagi. `InProcessJobRunner._prune_locked`
# son 100 isi tutuyordu; paylasimli depoda sayiya gore budamak yaris uretir,
# TTL ise her anahtarin kendi omrunu tasimasini sagliyor.
DEFAULT_TTL_SEC = 24 * 3600


class RedisJobRunner:
    """Paylasimli depo kullanan `JobRunner`.

    Bu sinif isi CALISTIRMIYOR: kuyruga koyuyor ve durumunu okuyor. Calistirma
    `src/jobs/worker.py` icindeki dongude oluyor. Ayrim bilincli -- web
    surecinin bir isi calistirmasi icin hicbir sebep yok ve ayni sinif iki isi
    birden yapsaydi "web tarafi da calistirir mi" sorusu yapilandirmaya
    kalirdi.
    """

    def __init__(self, client, prefix: str = DEFAULT_PREFIX, ttl_sec: int = DEFAULT_TTL_SEC):
        self._redis = client
        self._prefix = prefix
        self._ttl = ttl_sec

    # ----------------------------------------------------------- anahtarlar
    def _job_key(self, job_id: str) -> str:
        return f"{self._prefix}:job:{job_id}"

    def _events_key(self, job_id: str) -> str:
        return f"{self._prefix}:job:{job_id}:ev"

    def _closed_key(self, job_id: str) -> str:
        return f"{self._prefix}:job:{job_id}:end"

    def _cancel_key(self, job_id: str) -> str:
        return f"{self._prefix}:job:{job_id}:cx"

    def _channel(self, job_id: str) -> str:
        return f"{self._prefix}:job:{job_id}:ch"

    @property
    def queue_key(self) -> str:
        return f"{self._prefix}:queue"

    def _touch(self, job_id: str) -> None:
        """Ise ait TUM anahtarlarin omrunu tazeler.

        Hepsi birlikte: biri once suresi dolarsa tutamac var ama olaylari yok
        (ya da tersi) gibi tutarsiz bir ara duruma dusulurdu.
        """
        pipe = self._redis.pipeline()
        for key in (
            self._job_key(job_id),
            self._events_key(job_id),
            self._closed_key(job_id),
            self._cancel_key(job_id),
        ):
            pipe.expire(key, self._ttl)
        pipe.execute()

    # ------------------------------------------------------------------ API
    def submit(
        self, job_id: str, user_id: str, task: str, payload: dict[str, Any] | None = None
    ) -> JobHandle:
        # Is adi BURADA cozuluyor: bilinmeyen bir ad, kuyruga girip worker'da
        # patlamak yerine cagirana hemen hata olarak donmeli. Web ve worker
        # ayni kod tabanini calistirdigi icin bu kontrol anlamli.
        resolve_task(task)

        handle = JobHandle(job_id=job_id, user_id=user_id)
        envelope = json.dumps(
            {"job_id": job_id, "user_id": user_id, "task": task, "payload": payload or {}},
            ensure_ascii=False,
        )

        pipe = self._redis.pipeline()
        pipe.hset(
            self._job_key(job_id),
            mapping={
                "job_id": job_id,
                "user_id": user_id,
                "state": handle.state.value,
                "created_at": handle.created_at,
                "progress": "0.0",
                "stage": "",
                "message": "",
                "error": "",
            },
        )
        # Zarf kuyruga tutamactan SONRA yaziliyor: worker isi kuyruktan
        # aldiginda tutamac zaten okunabilir olmali, yoksa "calisan ama
        # gorunmeyen is" penceresi olusur.
        pipe.rpush(self.queue_key, envelope)
        pipe.execute()
        self._touch(job_id)
        return handle

    def get(self, job_id: str) -> JobHandle | None:
        raw = self._redis.hgetall(self._job_key(job_id))
        if not raw:
            return None
        data = {_text(k): _text(v) for k, v in raw.items()}
        return _handle_from(data)

    def result(self, job_id: str, timeout: float | None = None) -> Any:
        """DESTEKLENMIYOR -- ve bu bir eksiklik degil.

        Isin donus degeri surec sinirini gecemez. Uretimde de zaten gecmiyordu:
        `result()` API katmaninda HIC cagrilmiyor, sonuc SQLite'tan
        (`store.get_run`) okunuyor cunku `build_playlist` onu `finalize_run`
        ile yaziyor. Sessizce `None` donmek, cagirani "is sonucsuz bitti"
        sanmaya iterdi.
        """
        raise NotImplementedError(
            "RedisJobRunner is sonucunu tasimaz; sonuc kalici depodan okunur "
            "(bkz. `SQLiteStore.get_run`)."
        )

    def events_since(self, job_id: str, cursor: int = 0) -> list[tuple[int, ProgressEvent]]:
        raw = self._redis.lrange(self._events_key(job_id), cursor, -1)
        events: list[tuple[int, ProgressEvent]] = []
        for offset, item in enumerate(raw or []):
            try:
                events.append((cursor + offset, ProgressEvent.model_validate_json(_text(item))))
            except Exception:
                # Bozuk tek bir satir AKISI KESMEMELI: imlec ilerlemezse
                # istemci sonsuza kadar ayni noktada takilir.
                _log.warning("Okunamayan ilerleme olayi atlandi: %s/%s", job_id, cursor + offset)
        return events

    def wait_for_events(self, job_id: str, cursor: int, timeout: float) -> bool:
        """Yeni olay ya da kapanis bekler. Zaman asiminda `False`.

        ONCE abone olunuyor, SONRA durum kontrol ediliyor: ters sirada,
        kontrol ile abonelik arasinda gelen bir olay kacirilir ve akis
        gereksiz yere bir heartbeat suresi bekler.
        """
        if self._has_news(job_id, cursor):
            return True

        pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        try:
            pubsub.subscribe(self._channel(job_id))
            # Abonelikten SONRA tekrar bak: arada gelmis olabilir.
            if self._has_news(job_id, cursor):
                return True
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                if pubsub.get_message(timeout=min(remaining, 1.0)):
                    if self._has_news(job_id, cursor):
                        return True
                elif self._has_news(job_id, cursor):
                    # Bildirim kacirilmis olabilir; periyodik dogrulama.
                    return True
        finally:
            try:
                pubsub.close()
            except Exception:
                pass

    def _has_news(self, job_id: str, cursor: int) -> bool:
        pipe = self._redis.pipeline()
        pipe.llen(self._events_key(job_id))
        pipe.exists(self._closed_key(job_id))
        length, closed = pipe.execute()
        return int(length or 0) > cursor or bool(closed)

    def is_stream_closed(self, job_id: str) -> bool:
        if self._redis.exists(self._closed_key(job_id)):
            return True
        # Bilinmeyen is icin de KAPALI: silinmis ya da suresi dolmus bir isin
        # akisini bekleyen istemci sonsuza kadar asili kalmamali.
        return not self._redis.exists(self._job_key(job_id))

    def events(self, job_id: str, timeout: float = 30.0) -> Iterator[ProgressEvent]:
        cursor = 0
        while True:
            for index, event in self.events_since(job_id, cursor):
                cursor = index + 1
                yield event
            if self.is_stream_closed(job_id):
                return
            self.wait_for_events(job_id, cursor, timeout)

    def cancel(self, job_id: str) -> bool:
        """Iptal ISTER. Calisan is bunu bir sonraki ilerleme bildiriminde ogrenir."""
        handle = self.get(job_id)
        if handle is None or handle.state.is_terminal:
            return False
        self._redis.set(self._cancel_key(job_id), "1", ex=self._ttl)
        # Henuz BASLAMAMIS is: worker onu hic almayacak, o yuzden durumu
        # burada sonlandirip akisi kapatiyoruz -- dinleyiciler takilmasin.
        # (Zarf kuyrukta kalabilir; worker `_claim` sirasinda iptali gorup atar.)
        if handle.state is JobState.PENDING:
            self._finish(job_id, JobState.CANCELLED, error=None)
        return True

    def shutdown(self, wait: bool = True) -> None:
        """Web tarafinda calisan bir havuz YOK; kapatilacak sey de yok."""
        return None

    # ------------------------------------------------- worker tarafi yazimlar
    #
    # Bu metotlar `src/jobs/worker.py` tarafindan cagriliyor. Ayni sinifta
    # duruyorlar cunku ANAHTAR DUZENI burada tanimli; iki yere bolmek, duzenin
    # iki yerde bilinmesi demekti.

    def claim(self, timeout: float = 5.0) -> dict[str, Any] | None:
        """Kuyruktan bir zarf alir. Bos donerse zaman asimi olmustur."""
        item = self._redis.blpop([self.queue_key], timeout=int(timeout))
        if not item:
            return None
        try:
            return json.loads(_text(item[1]))
        except ValueError:
            _log.warning("Okunamayan is zarfi atildi")
            return None

    def is_cancelled(self, job_id: str) -> bool:
        return bool(self._redis.exists(self._cancel_key(job_id)))

    def mark_running(self, job_id: str) -> None:
        self._redis.hset(self._job_key(job_id), "state", JobState.RUNNING.value)
        self._publish(job_id)

    def append_event(self, job_id: str, event: ProgressEvent) -> None:
        pipe = self._redis.pipeline()
        pipe.rpush(self._events_key(job_id), event.model_dump_json())
        pipe.hset(
            self._job_key(job_id),
            mapping={
                "progress": str(event.progress),
                "stage": event.stage,
                "message": event.message,
            },
        )
        pipe.execute()
        self._touch(job_id)
        self._publish(job_id)

    def finish(self, job_id: str, state: JobState, error: str | None = None) -> None:
        self._finish(job_id, state, error)

    def _finish(self, job_id: str, state: JobState, error: str | None) -> None:
        pipe = self._redis.pipeline()
        pipe.hset(
            self._job_key(job_id),
            mapping={"state": state.value, "error": error or ""},
        )
        pipe.set(self._closed_key(job_id), "1", ex=self._ttl)
        pipe.execute()
        self._touch(job_id)
        self._publish(job_id)

    def _publish(self, job_id: str) -> None:
        # Bildirim BEST-EFFORT: veri her zaman LIST'te ve `wait_for_events`
        # periyodik olarak da bakiyor. Bildirimin kaybi gecikme yaratir,
        # VERI KAYBI yaratmaz.
        try:
            self._redis.publish(self._channel(job_id), "1")
        except Exception:
            _log.debug("Bildirim yayinlanamadi: %s", job_id, exc_info=True)


def _text(value: Any) -> str:
    """Redis istemcisi `decode_responses` acik ya da kapali olabilir."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _handle_from(data: dict[str, str]) -> JobHandle:
    handle = JobHandle(
        job_id=data.get("job_id", ""),
        user_id=data.get("user_id", ""),
        state=JobState(data.get("state") or JobState.PENDING.value),
        created_at=data.get("created_at") or "",
        error=data.get("error") or None,
    )
    # `snapshot()` ilerlemeyi `last_event` uzerinden okuyor; hash'teki
    # alanlardan en son olayin ozetini geri kuruyoruz. Olayin TAMAMI degil --
    # gerekli olan yalnizca bu uc alan ve tam olay gunlukte zaten duruyor.
    if data.get("stage"):
        handle.last_event = ProgressEvent(
            stage=data.get("stage", ""),
            message=data.get("message", ""),
            progress=float(data.get("progress") or 0.0),
        )
    return handle
