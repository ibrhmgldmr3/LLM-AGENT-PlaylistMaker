from src.config import AppConfig
from src.models import TranscriptResult, VideoCandidate
from src.services import transcript_service
from src.storage import SQLiteStore


def _config(tmp_path):
    config = AppConfig(
        gemini_api_key="test",
        gemini_model="gemini-test",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
        retry_max_attempts=1,
    )
    config.ensure_directories()
    return config


def _candidate():
    return VideoCandidate(
        video_id="abc123def45",
        url="https://www.youtube.com/watch?v=abc123def45",
        title="Python functions explained",
        language="en",
    )


def test_transcript_provider_fallback_and_cache_reuse(tmp_path, monkeypatch):
    config = _config(tmp_path)
    store = SQLiteStore(config.sqlite_path)
    state = transcript_service.RunTranscriptState()
    candidate = _candidate()

    calls = {"youtube": 0, "ytdlp": 0, "asr": 0}

    def fake_youtube(*args, **kwargs):
        calls["youtube"] += 1
        raise RuntimeError("blocked")

    def fake_ytdlp(*args, **kwargs):
        calls["ytdlp"] += 1
        return TranscriptResult(video_id=candidate.video_id, status="unavailable", source="yt_dlp_subtitles")

    def fake_asr(*args, **kwargs):
        calls["asr"] += 1
        return TranscriptResult(
            video_id=candidate.video_id,
            status="available",
            source="asr",
            backend="faster_whisper",
            text="python functions scope parameters return values",
        )

    monkeypatch.setattr(transcript_service, "_fetch_youtube_transcript", fake_youtube)
    monkeypatch.setattr(transcript_service, "_fetch_ytdlp_subtitles", fake_ytdlp)
    monkeypatch.setattr(transcript_service, "_fetch_asr_transcript", fake_asr)

    first = transcript_service.get_transcript(config, store, candidate, str(tmp_path), state)
    second = transcript_service.get_transcript(config, store, candidate, str(tmp_path), state)

    assert first.status == "available"
    assert second.status == "available"
    assert second.backend == "faster_whisper"
    assert calls == {"youtube": 1, "ytdlp": 1, "asr": 1}


def test_provider_cooldown_skips_repeated_requests(tmp_path, monkeypatch):
    config = _config(tmp_path)
    store = SQLiteStore(config.sqlite_path)
    state = transcript_service.RunTranscriptState()
    candidate = _candidate()

    store.mark_provider_cooldown("youtube_transcript_api", "rate limited", config.provider_cooldown_sec)
    calls = {"youtube": 0, "ytdlp": 0, "asr": 0}

    monkeypatch.setattr(transcript_service, "_fetch_youtube_transcript", lambda *args, **kwargs: calls.__setitem__("youtube", calls["youtube"] + 1))

    def fake_ytdlp(*args, **kwargs):
        calls["ytdlp"] += 1
        return TranscriptResult(video_id=candidate.video_id, status="unavailable", source="yt_dlp_subtitles")

    def fake_asr(*args, **kwargs):
        calls["asr"] += 1
        return TranscriptResult(
            video_id=candidate.video_id,
            status="available",
            source="asr",
            backend="whisper.cpp",
            text="python transcript from asr backend",
        )

    monkeypatch.setattr(transcript_service, "_fetch_ytdlp_subtitles", fake_ytdlp)
    monkeypatch.setattr(transcript_service, "_fetch_asr_transcript", fake_asr)

    result = transcript_service.get_transcript(config, store, candidate, str(tmp_path), state)

    assert result.status == "available"
    assert calls["youtube"] == 0
    assert calls["ytdlp"] == 1
    assert calls["asr"] == 1


def test_asr_backend_selection(tmp_path, monkeypatch):
    config = _config(tmp_path)

    monkeypatch.setattr(transcript_service.FasterWhisperProvider, "is_available", lambda self: True)
    monkeypatch.setattr(transcript_service.WhisperCppProvider, "is_available", lambda self: True)
    assert transcript_service.select_transcription_backend(config).name == "faster_whisper"

    config.asr_backend = "whisper.cpp"
    assert transcript_service.select_transcription_backend(config).name == "whisper.cpp"

    config.asr_backend = "faster-whisper"
    assert transcript_service.select_transcription_backend(config).name == "faster_whisper"
