"""faster-whisper'in kod cozme butcesi.

whisper.cpp ayri bir surec oldugu icin `subprocess timeout` ile
sinirlanabiliyordu; faster-whisper surec icinde calisiyor ve hicbir tavani
yoktu. `ASR_BACKEND=auto` varsayilan olarak faster-whisper'i sectigi icin
pratikte VARSAYILAN arka uc sinirsizdi: asili kalan tek bir is transkript
worker'ini suresiz blokluyor, calistirma hic ilerlemiyordu.
"""

from __future__ import annotations

import pytest

from src.config import AppConfig
from src.providers import faster_whisper_provider
from src.providers.faster_whisper_provider import (
    FasterWhisperProvider,
    _to_segments,
    _TranscriptionTimeout,
)


LONG_TEXT = "bu segment metni MIN_TRANSCRIPT_CHARS esigini asmak icin yeterince uzun tutuluyor"


class FakeSegment:
    def __init__(self, text, start=0.0, end=3.0):
        self.text = text
        self.start = start
        self.end = end


class FakeInfo:
    language = "tr"


def _config(tmp_path, **overrides):
    values = dict(
        gemini_api_key="test",
        gemini_model="gemini-test",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
    )
    values.update(overrides)
    return AppConfig(**values)


class FakeClock:
    """Kontrollu `time.monotonic`. Gercek `sleep` kullanmiyoruz: testin
    suresi, olctugu butceyle orantili olmamali."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_segments_are_converted_when_there_is_no_deadline():
    segments = _to_segments([FakeSegment("bir"), FakeSegment("iki", 3.0, 6.0)])
    assert [s.text for s in segments] == ["bir", "iki"]


def test_expired_deadline_stops_the_decode_loop(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(faster_whisper_provider.time, "monotonic", clock)

    with pytest.raises(_TranscriptionTimeout):
        _to_segments([FakeSegment("bir")], deadline=clock.now - 1)


def test_deadline_is_checked_between_segments(monkeypatch):
    """Uretec TEMBEL: her segmentte saate bakmak gercekten isi kesiyor."""
    clock = FakeClock()
    monkeypatch.setattr(faster_whisper_provider.time, "monotonic", clock)
    deadline = clock.now + 10

    consumed = []

    def slow_segments():
        for index in range(5):
            consumed.append(index)
            clock.advance(4)  # her segment butcenin bir parcasini yiyor
            yield FakeSegment(f"segment {index}")

    with pytest.raises(_TranscriptionTimeout):
        _to_segments(slow_segments(), deadline=deadline)

    # 3. segmentte butce asiliyor; uretec sonuna kadar TUKETILMIYOR.
    assert len(consumed) < 5


def test_transcribe_reports_timeout_as_temporary_failure(tmp_path, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(faster_whisper_provider.time, "monotonic", clock)

    class FakeModel:
        def transcribe(self, audio_path, **kwargs):
            def generator():
                for index in range(10):
                    clock.advance(60)
                    yield FakeSegment(f"{LONG_TEXT} {index}")

            return generator(), FakeInfo()

    config = _config(tmp_path, faster_whisper_timeout_sec=100)
    provider = FasterWhisperProvider(config)
    monkeypatch.setattr(FasterWhisperProvider, "_load_model", lambda self: FakeModel())

    result = provider.transcribe("ses.wav", "abc123def45", "tr")

    assert result.status == "failed_temporary"
    assert "100" in (result.error or "")
    # Kismi cikti sizmamali: yarim transkript, hic transkript olmamasindan
    # daha yaniltici olurdu.
    assert not result.text
    assert not result.segments


def test_backend_self_limit_wins_over_the_caller_wait(tmp_path):
    """Arka ucun TEMIZ durusu, cagiranin beklemesinden once gelmeli.

    Iki zaman asimi ayni degeri kullanirsa dis bekleme once doluyor -- sayaci
    daha erken basliyor (thread kurulumu, model yuklemesi) -- ve her seferinde
    terk edilmis daemon thread yoluna sapiliyordu. Pay bunu duzeltiyor.
    """
    import time as real_time

    from src.services import transcript_service

    class SelfLimitingBackend:
        name = "faster_whisper"

        def transcribe(self, audio_path, video_id, language=None):
            # Kendi butcesini (1 sn) asiyor ama TEMIZ donuyor -- gercek arka
            # uclarin zaman asiminda yaptigi sey bu.
            real_time.sleep(1.5)
            return TranscriptResultStub()

    class TranscriptResultStub:
        status = "failed_temporary"
        error = "faster-whisper 1 sn içinde tamamlanmadı"

    config = _config(tmp_path, faster_whisper_timeout_sec=1, max_asr_workers=1)

    result, timed_out, worker = transcript_service._transcribe_with_timeout(
        SelfLimitingBackend(), "ses.wav", "abc123def45", "tr", config
    )

    assert timed_out is False, "dis bekleme once dolmamali"
    assert worker is None, "terk edilmis daemon thread olusmamali"
    assert result.error == "faster-whisper 1 sn içinde tamamlanmadı"


def test_failed_thread_start_releases_the_asr_gate(tmp_path, monkeypatch):
    """Thread baslatilamazsa izin geri verilmeli.

    `work` hic calismadigi icin onun `finally` dali da calismaz. Sizinti
    KALICI: varsayilan `MAX_ASR_WORKERS=1` ile kapi sonsuza kadar kapali
    kalir ve sonraki her ASR istegi "worker unavailable" doner.
    """
    import threading

    from src.services import transcript_service

    workers = 9  # bu teste ozel semafor (kapi onbellegi max_workers ile anahtarli)

    class UnusedBackend:
        name = "faster_whisper"

        def transcribe(self, audio_path, video_id, language=None):
            raise AssertionError("thread hic baslamadi")

    monkeypatch.setattr(
        threading.Thread,
        "start",
        lambda self: (_ for _ in ()).throw(RuntimeError("can't start new thread")),
    )

    config = _config(tmp_path, faster_whisper_timeout_sec=5, max_asr_workers=workers)
    with pytest.raises(RuntimeError):
        transcript_service._transcribe_with_timeout(
            UnusedBackend(), "ses.wav", "abc123def45", "tr", config
        )

    gate = transcript_service._asr_gate(workers)
    acquired = [gate.acquire(blocking=False) for _ in range(workers)]
    for _ in range(sum(acquired)):
        gate.release()

    assert all(acquired), "kapinin izinleri eksik kalmis"


def test_transcribe_succeeds_within_budget(tmp_path, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(faster_whisper_provider.time, "monotonic", clock)

    class FakeModel:
        def transcribe(self, audio_path, **kwargs):
            return iter([FakeSegment(LONG_TEXT)]), FakeInfo()

    config = _config(tmp_path, faster_whisper_timeout_sec=1800)
    provider = FasterWhisperProvider(config)
    monkeypatch.setattr(FasterWhisperProvider, "_load_model", lambda self: FakeModel())

    result = provider.transcribe("ses.wav", "abc123def45", "tr")

    assert result.status == "available"
    assert result.segments
    assert result.backend == "faster_whisper"
