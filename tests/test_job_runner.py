"""Faz 0: is yurutme soyutlamasi.

API katmani isi arka plana atip cagirana hemen is numarasi donebilmeli,
ilerlemeyi ayri kanaldan akitmali.
"""

import threading
import time

import pytest

from src.jobs import InProcessJobRunner, JobCancelled, JobState, new_job_id
from src.models import ProgressEvent


@pytest.fixture
def runner():
    instance = InProcessJobRunner(max_workers=2)
    yield instance
    instance.shutdown(wait=False)


def _event(progress: float, stage: str = "test") -> ProgressEvent:
    return ProgressEvent(stage=stage, message=f"adim {progress}", progress=progress)


def test_submit_returns_immediately(runner):
    """Bu isin butun amaci: cagiran beklemeyecek."""
    job_id = new_job_id()

    def slow(emit):
        time.sleep(0.4)
        return "bitti"

    start = time.perf_counter()
    handle = runner.submit(job_id, "local", slow)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.1, "submit bloklamamali"
    assert handle.job_id == job_id
    assert runner.result(job_id, timeout=5) == "bitti"


def test_events_stream_in_order(runner):
    job_id = new_job_id()

    def work(emit):
        for value in (0.25, 0.5, 0.75, 1.0):
            emit(_event(value))
        return "ok"

    runner.submit(job_id, "local", work)
    received = [event.progress for event in runner.events(job_id, timeout=5)]

    assert received == [0.25, 0.5, 0.75, 1.0]


def test_event_stream_terminates_when_work_finishes(runner):
    """Akis kendiliginden bitmeli; SSE ucu sonsuza kadar beklememeli."""
    job_id = new_job_id()
    runner.submit(job_id, "local", lambda emit: emit(_event(1.0)))

    events = list(runner.events(job_id, timeout=5))

    assert len(events) == 1


def test_failure_is_recorded_and_stream_closes(runner):
    job_id = new_job_id()

    def broken(emit):
        emit(_event(0.5))
        raise RuntimeError("patladi")

    runner.submit(job_id, "local", broken)
    events = list(runner.events(job_id, timeout=5))

    assert len(events) == 1
    with pytest.raises(RuntimeError):
        runner.result(job_id, timeout=5)
    handle = runner.get(job_id)
    assert handle.state is JobState.FAILED
    assert "patladi" in handle.error


def test_cancel_stops_a_running_job(runner):
    """Calisan is iptali bir sonraki ilerleme bildiriminde ogrenir."""
    job_id = new_job_id()
    started = []

    def long_work(emit):
        for value in range(100):
            started.append(value)
            emit(_event(min(value / 100, 1.0)))
            time.sleep(0.02)
        return "hic bitmemeli"

    runner.submit(job_id, "local", long_work)
    while not started:
        time.sleep(0.01)
    assert runner.cancel(job_id) is True

    with pytest.raises(JobCancelled):
        runner.result(job_id, timeout=5)
    assert runner.get(job_id).state is JobState.CANCELLED
    assert len(started) < 100


def test_cancel_unknown_job_is_false(runner):
    assert runner.cancel("olmayan-is") is False


def test_handle_snapshot_tracks_progress(runner):
    job_id = new_job_id()

    def work(emit):
        emit(ProgressEvent(stage="arama", message="adaylar", progress=0.4))
        return "ok"

    runner.submit(job_id, "local", work)
    runner.result(job_id, timeout=5)

    snapshot = runner.get(job_id).snapshot()
    assert snapshot["job_id"] == job_id
    assert snapshot["user_id"] == "local"
    assert snapshot["state"] == "done"
    assert snapshot["progress"] == 0.4
    assert snapshot["stage"] == "arama"
    assert snapshot["error"] is None


def test_jobs_run_concurrently(runner):
    """2 worker ile iki is paralel ilerlemeli."""
    ids = [new_job_id() for _ in range(2)]

    def work(emit):
        time.sleep(0.3)
        return "ok"

    start = time.perf_counter()
    for job_id in ids:
        runner.submit(job_id, "local", work)
    for job_id in ids:
        runner.result(job_id, timeout=5)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.55, f"isler seri calisti ({elapsed:.2f} sn)"


def test_every_subscriber_receives_every_event(runner):
    """Regresyon: olaylar tek bir `queue.Queue`'dan YIKICI okunuyordu.

    Iki tarayici sekmesi ayni calistirmayi izlediginde olaylar aralarinda
    bolunuyor ve her biri ilerlemenin yarisini goruyordu.
    """
    job_id = new_job_id()
    ready = threading.Event()

    def work(emit):
        ready.wait(2)
        for value in (0.2, 0.4, 0.6, 0.8, 1.0):
            emit(_event(value))
            time.sleep(0.02)
        return "ok"

    runner.submit(job_id, "local", work)

    first: list[float] = []
    second: list[float] = []

    def collect(sink):
        for event in runner.events(job_id, timeout=3):
            sink.append(event.progress)

    threads = [threading.Thread(target=collect, args=(sink,)) for sink in (first, second)]
    for thread in threads:
        thread.start()
    ready.set()
    for thread in threads:
        thread.join(timeout=5)

    expected = [0.2, 0.4, 0.6, 0.8, 1.0]
    assert first == expected, f"1. abone eksik aldi: {first}"
    assert second == expected, f"2. abone eksik aldi: {second}"


def test_late_subscriber_replays_from_the_beginning(runner):
    """Gec baglanan abone kacirdigi olaylari gunlukten alabilmeli."""
    job_id = new_job_id()
    runner.submit(job_id, "local", lambda emit: [emit(_event(v)) for v in (0.5, 1.0)])
    runner.result(job_id, timeout=5)

    replayed = [event.progress for event in runner.events(job_id, timeout=2)]

    assert replayed == [0.5, 1.0]


def test_events_since_supports_resume(runner):
    """`Last-Event-ID` bunun uzerine kurulu: imlecten sonrasini ver."""
    job_id = new_job_id()
    runner.submit(job_id, "local", lambda emit: [emit(_event(v)) for v in (0.25, 0.5, 0.75, 1.0)])
    runner.result(job_id, timeout=5)

    tail = runner.events_since(job_id, cursor=2)

    assert [index for index, _ in tail] == [2, 3]
    assert [event.progress for _, event in tail] == [0.75, 1.0]


def test_stream_closes_after_completion(runner):
    job_id = new_job_id()
    assert runner.is_stream_closed(job_id) is True  # bilinmeyen is
    runner.submit(job_id, "local", lambda emit: emit(_event(1.0)))
    runner.result(job_id, timeout=5)
    assert runner.is_stream_closed(job_id) is True


def test_old_jobs_are_pruned():
    """Bellekte sinirsiz is birikmemeli."""
    runner = InProcessJobRunner(max_workers=1, keep_last=5)
    try:
        for _ in range(20):
            job_id = new_job_id()
            runner.submit(job_id, "local", lambda emit: "ok")
            runner.result(job_id, timeout=5)
        assert len(runner._handles) <= 10
    finally:
        runner.shutdown(wait=False)
