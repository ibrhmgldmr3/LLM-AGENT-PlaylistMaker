"""Is hatasi istemciye giderken maskelenmeli.

Herkese acik bir serviste bu yol, yakalanmamis HER istisnanin kullaniciya
ulastigi ortak kanal: `GET /api/runs/{id}` 500 govdesi ve SSE `done` olayi.
Projedeki diger tum istemciye-cikan yollar (`api/main.py` istisna isleyicileri,
saglayici hatalari) maskeliyor; bu yol atlanmisti.
"""

from __future__ import annotations

import logging

import pytest

from src.jobs import InProcessJobRunner
from src.models import ProgressEvent

SIZAN_URL = "https://www.googleapis.com/youtube/v3/search?key=AIzaSyTOPSECRETVALUE123&q=x"


def _runner():
    runner = InProcessJobRunner(max_workers=1)
    try:
        yield runner
    finally:
        runner.shutdown(wait=True)


@pytest.fixture
def runner():
    yield from _runner()


def _wait(runner, job_id):
    with pytest.raises(Exception):
        runner.result(job_id, timeout=5)


def test_api_key_in_an_uncaught_exception_never_reaches_the_handle(runner, task):
    def patlayan(emit):
        raise RuntimeError(f"istek basarisiz: {SIZAN_URL}")

    runner.submit("is-1", "ali", task(patlayan))
    _wait(runner, "is-1")

    handle = runner.get("is-1")
    assert handle.state.value == "failed"
    assert "AIzaSyTOPSECRETVALUE123" not in handle.error
    assert "***" in handle.error
    # Maskeleme mesajin TAMAMINI yok etmemeli: kullanici ne oldugunu gorebilmeli.
    assert "istek basarisiz" in handle.error


def test_the_snapshot_that_feeds_sse_is_also_clean(runner, task):
    def patlayan(emit):
        raise RuntimeError(f"hata: {SIZAN_URL}")

    runner.submit("is-2", "ali", task(patlayan))
    _wait(runner, "is-2")

    snapshot = runner.get("is-2").snapshot()
    assert "AIzaSyTOPSECRETVALUE123" not in snapshot["error"]


def test_a_failing_job_leaves_a_traceback_in_the_server_log(runner, caplog, task):
    """Future hicbir zaman beklenmedigi icin is istisnalari hic loglanmiyordu:
    calistirma "failed" gorunuyor, sebebi hicbir yerde yazmiyordu."""

    def patlayan(emit):
        raise ValueError("ic hata")

    with caplog.at_level(logging.ERROR, logger="src.jobs.runner"):
        runner.submit("is-3", "ali", task(patlayan))
        _wait(runner, "is-3")

    kayitlar = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert kayitlar, "basarisiz is sunucu logunda iz birakmali"
    assert any(r.exc_info for r in kayitlar), "yigin izi tutulmali"


def test_a_successful_job_still_reports_no_error(runner, task):
    runner.submit("is-4", "ali", task(lambda emit: emit(
        ProgressEvent(stage="done", message="bitti", progress=1.0)
    )))
    runner.result("is-4", timeout=5)

    assert runner.get("is-4").error is None
