from src.config import AppConfig
from src.models import TranscriptResult, VideoCandidate
from src.providers.errors import ProviderTemporaryError, VideoUnavailableError
from src.services import transcript_service
from src.storage import SQLiteStore


def _config(tmp_path, **overrides):
    values = dict(
        gemini_api_key="test",
        gemini_model="gemini-test",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
        retry_max_attempts=1,
        retry_base_delay_sec=0.01,
        # Uretimde varsayilan KAPALI (yavas oldugu icin); bu dosyadaki testler
        # ozellikle ASR yedegini incelediginden acik baslatiyoruz.
        enable_asr_fallback=True,
    )
    values.update(overrides)
    config = AppConfig(**values)
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
        raise ProviderTemporaryError("blocked")

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

    def fake_youtube(*args, **kwargs):
        calls["youtube"] += 1
        raise AssertionError("cooldown'daki sağlayıcı çağrılmamalı")

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

    monkeypatch.setattr(transcript_service, "_fetch_youtube_transcript", fake_youtube)
    monkeypatch.setattr(transcript_service, "_fetch_ytdlp_subtitles", fake_ytdlp)
    monkeypatch.setattr(transcript_service, "_fetch_asr_transcript", fake_asr)

    result = transcript_service.get_transcript(config, store, candidate, str(tmp_path), state)

    assert result.status == "available"
    assert calls["youtube"] == 0
    assert calls["ytdlp"] == 1
    assert calls["asr"] == 1


def test_cooldown_is_not_written_into_the_transcript_cache(tmp_path, monkeypatch):
    """Regresyon: 15 dk'lik cooldown, 1 saatlik 'cooldown' onbellek kaydi birakiyordu."""
    config = _config(tmp_path)
    store = SQLiteStore(config.sqlite_path)
    state = transcript_service.RunTranscriptState()
    candidate = _candidate()

    store.mark_provider_cooldown("youtube_transcript_api", "rate limited", config.provider_cooldown_sec)
    monkeypatch.setattr(
        transcript_service,
        "_fetch_ytdlp_subtitles",
        lambda *a, **k: TranscriptResult(video_id=candidate.video_id, status="available",
                                         source="yt_dlp_subtitles", text="x" * 60),
    )

    transcript_service.get_transcript(config, store, candidate, str(tmp_path), state)

    assert store.get_transcript_cache(candidate.video_id, "youtube_transcript_api") is None


def test_video_level_error_does_not_penalise_the_provider(tmp_path, monkeypatch):
    """Regresyon: altyazisi kapali TEK bir video saglayiciyi 15 dk devre disi birakiyordu."""
    config = _config(tmp_path, provider_failure_threshold=1)
    store = SQLiteStore(config.sqlite_path)
    state = transcript_service.RunTranscriptState()
    candidate = _candidate()

    monkeypatch.setattr(
        transcript_service,
        "_fetch_youtube_transcript",
        lambda *a, **k: (_ for _ in ()).throw(VideoUnavailableError("Subtitles are disabled")),
    )
    monkeypatch.setattr(
        transcript_service,
        "_fetch_ytdlp_subtitles",
        lambda *a, **k: TranscriptResult(video_id=candidate.video_id, status="unavailable", source="yt_dlp_subtitles"),
    )
    monkeypatch.setattr(
        transcript_service,
        "_fetch_asr_transcript",
        lambda *a, **k: TranscriptResult(video_id=candidate.video_id, status="unavailable", source="asr"),
    )

    result = transcript_service.get_transcript(config, store, candidate, str(tmp_path), state)

    assert result.status == "unavailable"
    assert store.get_provider_cooldown("youtube_transcript_api") is None


def test_infra_error_counts_towards_provider_cooldown(tmp_path, monkeypatch):
    config = _config(tmp_path, provider_failure_threshold=1)
    store = SQLiteStore(config.sqlite_path)
    candidate = _candidate()

    monkeypatch.setattr(
        transcript_service,
        "_fetch_youtube_transcript",
        lambda *a, **k: (_ for _ in ()).throw(ProviderTemporaryError("IP blocked")),
    )
    monkeypatch.setattr(
        transcript_service,
        "_fetch_ytdlp_subtitles",
        lambda *a, **k: TranscriptResult(video_id=candidate.video_id, status="unavailable", source="yt_dlp_subtitles"),
    )
    monkeypatch.setattr(
        transcript_service,
        "_fetch_asr_transcript",
        lambda *a, **k: TranscriptResult(video_id=candidate.video_id, status="unavailable", source="asr"),
    )

    transcript_service.get_transcript(
        config, store, candidate, str(tmp_path), transcript_service.RunTranscriptState()
    )

    assert store.get_provider_cooldown("youtube_transcript_api") is not None


def test_asr_is_skipped_when_disabled(tmp_path, monkeypatch):
    config = _config(tmp_path, enable_asr_fallback=False)
    store = SQLiteStore(config.sqlite_path)
    candidate = _candidate()
    called = {"asr": 0}

    monkeypatch.setattr(
        transcript_service,
        "_fetch_youtube_transcript",
        lambda *a, **k: TranscriptResult(video_id=candidate.video_id, status="unavailable", source="youtube_transcript_api"),
    )
    monkeypatch.setattr(
        transcript_service,
        "_fetch_ytdlp_subtitles",
        lambda *a, **k: TranscriptResult(video_id=candidate.video_id, status="unavailable", source="yt_dlp_subtitles"),
    )
    monkeypatch.setattr(
        transcript_service, "_fetch_asr_transcript", lambda *a, **k: called.__setitem__("asr", called["asr"] + 1)
    )

    result = transcript_service.get_transcript(
        config, store, candidate, str(tmp_path), transcript_service.RunTranscriptState()
    )

    assert called["asr"] == 0
    assert result.status == "unavailable"
    assert "asr" not in result.attempted_providers


def test_asr_run_budget_is_enforced(tmp_path, monkeypatch):
    config = _config(tmp_path, max_asr_videos_per_run=1)
    store = SQLiteStore(config.sqlite_path)
    state = transcript_service.RunTranscriptState()
    asr_calls = {"count": 0}

    def fake_asr(config_, candidate_, run_dir, state_, language_hint, logger=None):
        asr_calls["count"] += 1
        return TranscriptResult(video_id=candidate_.video_id, status="unavailable", source="asr")

    monkeypatch.setattr(
        transcript_service,
        "_fetch_youtube_transcript",
        lambda c, l, lg=None: TranscriptResult(video_id=c.video_id, status="unavailable", source="youtube_transcript_api"),
    )
    monkeypatch.setattr(
        transcript_service,
        "_fetch_ytdlp_subtitles",
        lambda cfg, c, l, lg=None: TranscriptResult(video_id=c.video_id, status="unavailable", source="yt_dlp_subtitles"),
    )
    monkeypatch.setattr(transcript_service, "_fetch_asr_transcript", fake_asr)

    for index in range(3):
        candidate = VideoCandidate(video_id=f"video{index:07d}", url="https://x", title="t")
        transcript_service.get_transcript(config, store, candidate, str(tmp_path), state)

    assert asr_calls["count"] == 1


def test_asr_backend_selection(tmp_path, monkeypatch):
    config = _config(tmp_path)

    monkeypatch.setattr(transcript_service.FasterWhisperProvider, "is_available", lambda self: True)
    monkeypatch.setattr(transcript_service.WhisperCppProvider, "is_available", lambda self: True)
    assert transcript_service.select_transcription_backend(config).name == "faster_whisper"

    config.asr_backend = "whisper.cpp"
    assert transcript_service.select_transcription_backend(config).name == "whisper.cpp"

    config.asr_backend = "faster-whisper"
    assert transcript_service.select_transcription_backend(config).name == "faster_whisper"
