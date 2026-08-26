"""Uzun suren calistirmalar icin is yurutme soyutlamasi.

`build_playlist` 4-40 saniye (ASR acikken dakikalar) suruyor; bir HTTP istegi
bunu bekleyemez. API isi arka plana atip cagirana hemen bir is numarasi
donmeli, ilerlemeyi ayri bir kanaldan (SSE) akitmali.

Bu modulun tek amaci o davranisi BIR ARAYUZ ardina koymak. Bugun surec-ici
calisiyor; cok kullanicili moda gecildiginde Celery/RQ tabanli bir uygulama
eklenir ve cagiran kod degismez. FastAPI'nin `BackgroundTasks`'i servis
katmanina bilerek SIZDIRILMAZ.
"""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterator, Protocol

from src.jobs.tasks import TaskContext, resolve_task
from src.models import ProgressEvent
from src.utils.logging_utils import redact_secrets

_log = logging.getLogger(__name__)


class JobState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {JobState.DONE, JobState.FAILED, JobState.CANCELLED}


class JobCancelled(Exception):
    """Is iptal edildiginde calisan fonksiyonun icinden firlatilir."""


@dataclass
class JobHandle:
    """Bir isin disaridan gorunen durumu. Sonuc nesnesini TASIMAZ."""

    job_id: str
    user_id: str
    state: JobState = JobState.PENDING
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_event: ProgressEvent | None = None
    error: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "user_id": self.user_id,
            "state": self.state.value,
            "created_at": self.created_at,
            "progress": self.last_event.progress if self.last_event else 0.0,
            "stage": self.last_event.stage if self.last_event else None,
            "message": self.last_event.message if self.last_event else None,
            "error": self.error,
        }


class JobRunner(Protocol):
    """Cagiranin bilmesi gereken tek yuzey."""

    def submit(
        self, job_id: str, user_id: str, task: str, payload: dict[str, Any] | None = None
    ) -> JobHandle:
        """Isi kuyruga alir ve hemen doner.

        `task` KAYITLI bir is adi, `payload` ise JSON'a cevrilebilir girdisi
        (bkz. `src/jobs/tasks.py`). Eskiden buraya bir CLOSURE geciriliyordu;
        surec-ici calisirken sorunsuzdu ama closure surec sinirini gecemez,
        yani paylasimli kuyruk kullanan bir uygulama o imzayi hicbir zaman
        karsilayamazdi. Ad + veri, hem bu uygulamanin hem dagitik olanin
        karsilayabilecegi tek bicim.

        Bilinmeyen bir is adi HEMEN hata vermeli: cagiran taraf isi kabul edip
        202 dondukten sonra ogrenmemeli.
        """

    def get(self, job_id: str) -> JobHandle | None: ...

    def result(self, job_id: str, timeout: float | None = None) -> Any: ...

    def events_since(self, job_id: str, cursor: int) -> list[tuple[int, ProgressEvent]]:
        """`cursor` indeksinden itibaren BIRIKMIS olaylari dondurur (bloklamaz)."""

    def wait_for_events(self, job_id: str, cursor: int, timeout: float) -> bool:
        """Yeni olay ya da isin bitmesini bekler. Zaman asiminda False doner."""

    def is_stream_closed(self, job_id: str) -> bool:
        """Is bitti ve baska olay gelmeyecek mi?"""

    def cancel(self, job_id: str) -> bool:
        """Iptal ISTER. Henuz baslamamis is durdurulur; calisan is bir sonraki
        ilerleme bildiriminde `JobCancelled` alir."""


def new_job_id() -> str:
    return uuid.uuid4().hex


class _EventChannel:
    """Bir isin olay gunlugu.

    Kuyruk DEGIL, EKLEMELI LISTE. Kuyruk yikici okunur: iki abone (ornegin iki
    tarayici sekmesi) ayni isi izlediginde olaylar aralarinda bolunuyordu ve her
    biri ilerlemenin yarisini goruyordu. Gunlukte her abonenin kendi imleci var,
    bu sayede hem coklu abone hem `Last-Event-ID` ile devam etme calisiyor.
    """

    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []
        self.closed = False
        self._condition = threading.Condition()

    def append(self, event: ProgressEvent) -> None:
        with self._condition:
            self.events.append(event)
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self.closed = True
            self._condition.notify_all()

    def since(self, cursor: int) -> list[tuple[int, ProgressEvent]]:
        with self._condition:
            return list(enumerate(self.events[cursor:], start=cursor))

    def wait(self, cursor: int, timeout: float) -> bool:
        with self._condition:
            if cursor < len(self.events) or self.closed:
                return True
            return self._condition.wait(timeout)


class InProcessJobRunner:
    """Tek surecte calisan uygulama.

    Sinirlari bilerek belgeleniyor: is durumu bellekte tutulur, surec yeniden
    baslarsa kaybolur (tamamlanmis sonuclar SQLite'ta kalir), ve yatay
    olceklemede is baska bir surece dusebilir. Tek instance icin yeterli;
    cok kullanicili dagitimda `CeleryJobRunner` ile degistirilir.
    """

    def __init__(self, max_workers: int = 2, keep_last: int = 100):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="job")
        self._lock = threading.Lock()
        self._handles: dict[str, JobHandle] = {}
        self._channels: dict[str, _EventChannel] = {}
        self._futures: dict[str, Future] = {}
        self._cancelled: set[str] = set()
        self._keep_last = keep_last

    # ------------------------------------------------------------------ API
    def submit(
        self, job_id: str, user_id: str, task: str, payload: dict[str, Any] | None = None
    ) -> JobHandle:
        # Is adi SUBMIT ANINDA cozuluyor, calisma aninda degil: bilinmeyen bir
        # ad, cagirana 202 dondukten sonra arka planda patlayan bir is degil,
        # hemen goze carpan bir hata olmali.
        task_fn = resolve_task(task)
        context = TaskContext(job_id=job_id, user_id=user_id, payload=dict(payload or {}))

        handle = JobHandle(job_id=job_id, user_id=user_id)
        channel = _EventChannel()

        with self._lock:
            self._handles[job_id] = handle
            self._channels[job_id] = channel
            self._prune_locked()

        def emit(event: ProgressEvent) -> None:
            # Calisan is, iptal talebini bir sonraki ilerleme bildiriminde ogrenir.
            if job_id in self._cancelled:
                raise JobCancelled(job_id)
            handle.last_event = event
            channel.append(event)

        def run() -> Any:
            handle.state = JobState.RUNNING
            try:
                result = task_fn(context, emit)
                handle.state = JobState.DONE
                return result
            except JobCancelled:
                handle.state = JobState.CANCELLED
                raise
            except Exception as exc:
                handle.state = JobState.FAILED
                # MASKELENEREK saklaniyor: bu alan `GET /api/runs/{id}` yanitina
                # ve SSE `done` olayina girip DOGRUDAN istemciye gidiyor.
                # Saglayicilar kendi istisnalarini uretirken zaten maskeliyor
                # ama burasi yakalanmamis HER istisnanin gectigi ortak yol --
                # projedeki diger tum istemciye-cikan yollar (`api/main.py`
                # istisna isleyicileri) maskeliyor, bu yol atlanmisti.
                handle.error = redact_secrets(str(exc))
                # Yigin izi SUNUCUDA kalir. Future hicbir zaman beklenmedigi
                # icin is icindeki istisnalar buraya kadar hic loglanmiyordu:
                # calistirma "failed" gorunuyor, sebebi hicbir yerde yazmiyordu.
                _log.exception("Job %s failed", job_id)
                raise
            finally:
                channel.close()

        with self._lock:
            self._futures[job_id] = self._executor.submit(run)
        return handle

    def get(self, job_id: str) -> JobHandle | None:
        with self._lock:
            return self._handles.get(job_id)

    def result(self, job_id: str, timeout: float | None = None) -> Any:
        with self._lock:
            future = self._futures.get(job_id)
        if future is None:
            raise KeyError(job_id)
        return future.result(timeout=timeout)

    def events_since(self, job_id: str, cursor: int = 0) -> list[tuple[int, ProgressEvent]]:
        channel = self._channel(job_id)
        return channel.since(cursor) if channel else []

    def wait_for_events(self, job_id: str, cursor: int, timeout: float) -> bool:
        channel = self._channel(job_id)
        return channel.wait(cursor, timeout) if channel else True

    def is_stream_closed(self, job_id: str) -> bool:
        channel = self._channel(job_id)
        return channel.closed if channel else True

    def events(self, job_id: str, timeout: float = 30.0) -> Iterator[ProgressEvent]:
        """Kolaylik sarmalayicisi: is bitene kadar olaylari akitir.

        Testler ve senkron cagiranlar icin; SSE katmani imleci kendisi yonetir.
        """
        cursor = 0
        while True:
            for index, event in self.events_since(job_id, cursor):
                cursor = index + 1
                yield event
            if self.is_stream_closed(job_id):
                return
            self.wait_for_events(job_id, cursor, timeout)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            future = self._futures.get(job_id)
            handle = self._handles.get(job_id)
            channel = self._channels.get(job_id)
        if future is None or handle is None or handle.state.is_terminal:
            return False
        self._cancelled.add(job_id)
        if future.cancel():
            # Henuz baslamamisti; akisi kapat ki dinleyiciler takilmasin.
            handle.state = JobState.CANCELLED
            if channel:
                channel.close()
        return True

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)

    # -------------------------------------------------------------- ic isler
    def _channel(self, job_id: str) -> _EventChannel | None:
        with self._lock:
            return self._channels.get(job_id)

    def _prune_locked(self) -> None:
        """Bellekte sinirsiz is birikmesini onler."""
        if len(self._handles) <= self._keep_last:
            return
        terminal = [
            job_id for job_id, handle in self._handles.items() if handle.state.is_terminal
        ]
        for job_id in terminal[: len(self._handles) - self._keep_last]:
            self._handles.pop(job_id, None)
            self._channels.pop(job_id, None)
            self._futures.pop(job_id, None)
            self._cancelled.discard(job_id)
