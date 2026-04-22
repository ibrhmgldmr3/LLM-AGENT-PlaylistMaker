import requests

from src.config import AppConfig
from src.models import Transcript
from src.services import transcript_service


def test_transcript_cache_hit(tmp_path, monkeypatch):
    config = AppConfig(
        GEMINI_API_KEY="x",
        GEMINI_MODEL="test",
        YOUTUBE_SEARCH_PER_TOPIC=8,
        TRANSCRIPT_SCORE_TOP_K=3,
        REQUEST_TIMEOUT_SEC=10,
        RETRY_MAX_ATTEMPTS=1,
        RETRY_BASE_DELAY_SEC=0.1,
        DATA_DIR=str(tmp_path),
    )
    cache_path = tmp_path / "cache" / "transcripts" / "abc123def45.json"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text('{"video_id":"abc123def45","language":"en","source":"cache","text":"hello world","fetched_at":"now"}', encoding="utf-8")

    monkeypatch.setattr(transcript_service, "_fetch_youtube_transcript", lambda *args, **kwargs: None)
    monkeypatch.setattr(transcript_service, "_fetch_ytdlp_captions", lambda *args, **kwargs: None)
    monkeypatch.setattr(transcript_service, "_whisper_cpp_transcribe", lambda *args, **kwargs: None)

    t = transcript_service.get_transcript(config, "abc123def45", "https://www.youtube.com/watch?v=abc123def45", str(tmp_path))
    assert isinstance(t, Transcript)
    assert t.source == "cache"


def test_transcript_429_does_not_crash(tmp_path, monkeypatch):
    config = AppConfig(
        GEMINI_API_KEY="x",
        GEMINI_MODEL="test",
        YOUTUBE_SEARCH_PER_TOPIC=8,
        TRANSCRIPT_SCORE_TOP_K=3,
        REQUEST_TIMEOUT_SEC=10,
        RETRY_MAX_ATTEMPTS=1,
        RETRY_BASE_DELAY_SEC=0.1,
        DATA_DIR=str(tmp_path),
    )

    monkeypatch.setattr(transcript_service, "_fetch_youtube_transcript", lambda *args, **kwargs: None)

    def _raise_429(*args, **kwargs):
        raise RuntimeError("429 Client Error: Too Many Requests")

    monkeypatch.setattr(transcript_service, "_fetch_ytdlp_captions", _raise_429)
    monkeypatch.setattr(transcript_service, "_whisper_cpp_transcribe", lambda *args, **kwargs: None)

    t = transcript_service.get_transcript(
        config,
        "abc123def45",
        "https://www.youtube.com/watch?v=abc123def45",
        str(tmp_path),
    )
    assert t is None


def test_download_vtt_with_backoff_retries_429(monkeypatch):
    calls = {"count": 0}

    class _Resp:
        def __init__(self, status_code=200, text="", headers=None):
            self.status_code = status_code
            self.text = text
            self.headers = headers or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(response=self)

    def _fake_get(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return _Resp(status_code=429, headers={"Retry-After": "0"})
        return _Resp(status_code=200, text="WEBVTT\n\nhello world")

    monkeypatch.setattr(transcript_service.requests, "get", _fake_get)
    monkeypatch.setattr(transcript_service.time, "sleep", lambda *_: None)

    text = transcript_service._download_vtt_with_backoff("http://example.com/test.vtt", timeout_sec=2)
    assert "hello world" in text
    assert calls["count"] == 2


def test_parse_retry_after_seconds_invalid_values():
    assert transcript_service._parse_retry_after_seconds("1.5") == 1.5
    assert transcript_service._parse_retry_after_seconds("0") is None
    assert transcript_service._parse_retry_after_seconds("-1") is None
    assert transcript_service._parse_retry_after_seconds("abc") is None
