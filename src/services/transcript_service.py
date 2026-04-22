from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from src.config import AppConfig
from src.models import TranscriptResult, VideoCandidate
from src.providers.faster_whisper_provider import FasterWhisperProvider
from src.providers.whisper_cpp_provider import WhisperCppProvider
from src.providers.youtube_transcript_api_provider import YouTubeTranscriptAPIProvider
from src.providers.ytdlp_provider import YtDlpProvider
from src.storage import SQLiteStore
from src.utils.retry_utils import retry_with_backoff


@dataclass
class RunTranscriptState:
    attempted_pairs: set[tuple[str, str]] = field(default_factory=set)
    downloaded_audio: dict[str, str] = field(default_factory=dict)


def select_transcription_backend(config: AppConfig):
    faster = FasterWhisperProvider(config)
    whisper_cpp = WhisperCppProvider(config)
    if config.asr_backend == "faster-whisper":
        return faster if faster.is_available() else None
    if config.asr_backend == "whisper.cpp":
        return whisper_cpp if whisper_cpp.is_available() else None
    if faster.is_available():
        return faster
    if whisper_cpp.is_available():
        return whisper_cpp
    return None


def get_transcript(
    config: AppConfig,
    store: SQLiteStore,
    candidate: VideoCandidate,
    run_dir: str,
    state: RunTranscriptState,
    logger=None,
) -> TranscriptResult:
    providers = [
        ("youtube_transcript_api", lambda: _fetch_youtube_transcript(candidate, logger)),
        ("yt_dlp_subtitles", lambda: _fetch_ytdlp_subtitles(config, candidate, logger)),
        ("asr", lambda: _fetch_asr_transcript(config, candidate, run_dir, state, logger)),
    ]
    attempted: list[str] = []
    for provider_name, loader in providers:
        attempted.append(provider_name)
        cached = store.get_transcript_cache(candidate.video_id, provider_name)
        if cached is not None:
            cached.attempted_providers = attempted.copy()
            if cached.status == "available":
                return cached
            continue

        if store.get_provider_cooldown(provider_name):
            result = TranscriptResult(
                video_id=candidate.video_id,
                status="cooldown",
                source=provider_name,
                attempted_providers=attempted.copy(),
            )
            store.put_transcript_cache(result, config.failure_cache_ttl_sec)
            continue

        if (candidate.video_id, provider_name) in state.attempted_pairs:
            continue
        state.attempted_pairs.add((candidate.video_id, provider_name))

        try:
            result = retry_with_backoff(
                loader,
                attempts=config.retry_max_attempts,
                base_delay=config.retry_base_delay_sec,
                logger=logger,
                on_exception=(RuntimeError,),
            )
        except Exception as exc:
            store.mark_provider_cooldown(provider_name, str(exc), config.provider_cooldown_sec)
            result = TranscriptResult(
                video_id=candidate.video_id,
                status="failed_temporary",
                source=provider_name,
                error=str(exc),
                attempted_providers=attempted.copy(),
            )
            store.put_transcript_cache(result, config.failure_cache_ttl_sec)
            continue

        result.attempted_providers = attempted.copy()
        ttl = config.transcript_cache_ttl_sec if result.status == "available" else config.failure_cache_ttl_sec
        store.put_transcript_cache(result, ttl)
        if result.status == "available":
            store.clear_provider_cooldown(provider_name)
            return result

    return TranscriptResult(
        video_id=candidate.video_id,
        status="unavailable",
        source="none",
        attempted_providers=attempted,
        error="No transcript provider produced usable content",
    )


def _fetch_youtube_transcript(candidate: VideoCandidate, logger=None) -> TranscriptResult:
    provider = YouTubeTranscriptAPIProvider()
    transcript = provider.fetch(candidate.video_id, candidate.language)
    if transcript:
        if logger:
            logger.info("Transcript resolved via %s for %s", provider.name, candidate.video_id)
        return transcript
    return TranscriptResult(video_id=candidate.video_id, status="unavailable", source=provider.name)


def _fetch_ytdlp_subtitles(config: AppConfig, candidate: VideoCandidate, logger=None) -> TranscriptResult:
    provider = YtDlpProvider(config)
    transcript = provider.fetch_subtitles(candidate.url, candidate.video_id)
    if transcript:
        if logger:
            logger.info("Transcript resolved via yt-dlp subtitles for %s", candidate.video_id)
        return transcript
    return TranscriptResult(video_id=candidate.video_id, status="unavailable", source="yt_dlp_subtitles")


def _fetch_asr_transcript(
    config: AppConfig,
    candidate: VideoCandidate,
    run_dir: str,
    state: RunTranscriptState,
    logger=None,
) -> TranscriptResult:
    backend = select_transcription_backend(config)
    if backend is None:
        return TranscriptResult(
            video_id=candidate.video_id,
            status="failed_permanent",
            source="asr",
            error="No ASR backend is available",
        )

    ytdlp = YtDlpProvider(config)
    audio_dir = Path(run_dir) / "audio"
    if candidate.video_id not in state.downloaded_audio:
        state.downloaded_audio[candidate.video_id] = ytdlp.download_audio(candidate.url, str(audio_dir), candidate.video_id)
    audio_path = state.downloaded_audio[candidate.video_id]
    if logger:
        logger.info("Using ASR backend %s for %s", backend.name, candidate.video_id)
    result = backend.transcribe(audio_path, candidate.video_id)
    result.source = "asr"
    result.backend = backend.name
    if result.status != "available" and logger:
        logger.warning("ASR backend %s returned %s for %s", backend.name, result.status, candidate.video_id)
    if os.path.exists(audio_path):
        try:
            os.remove(audio_path)
        except OSError:
            pass
    return result
