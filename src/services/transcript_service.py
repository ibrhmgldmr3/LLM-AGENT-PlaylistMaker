from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from src.config import AppConfig
from src.models import TranscriptResult, VideoCandidate
from src.providers.errors import (
    ProviderPermanentError,
    ProviderRateLimitedError,
    ProviderTemporaryError,
    VideoUnavailableError,
)
from src.providers.faster_whisper_provider import FasterWhisperProvider
from src.providers.whisper_cpp_provider import WhisperCppProvider
from src.providers.youtube_transcript_api_provider import YouTubeTranscriptAPIProvider
from src.providers.ytdlp_provider import YtDlpProvider
from src.storage import DEFAULT_USER_ID, SQLiteStore
from src.utils.logging_utils import redact_secrets
from src.utils.retry_utils import retry_with_backoff


@dataclass
class RunTranscriptState:
    attempted_pairs: set[tuple[str, str]] = field(default_factory=set)
    downloaded_audio: dict[str, str] = field(default_factory=dict)
    asr_video_count: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def claim_attempt(self, video_id: str, provider_name: str) -> bool:
        """Bu (video, saglayici) cifti icin denemeyi rezerve eder; ilk cagirana True doner."""
        with self.lock:
            key = (video_id, provider_name)
            if key in self.attempted_pairs:
                return False
            self.attempted_pairs.add(key)
            return True

    def claim_asr_slot(self, limit: int) -> bool:
        """Calistirma basina ASR kotasindan bir yer ayirir."""
        with self.lock:
            if limit and self.asr_video_count >= limit:
                return False
            self.asr_video_count += 1
            return True


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
    preferred_language: str | None = None,
    user_id: str = DEFAULT_USER_ID,
) -> TranscriptResult:
    language_hint = preferred_language or candidate.language
    providers: list[tuple[str, object]] = [
        ("youtube_transcript_api", lambda: _fetch_youtube_transcript(candidate, language_hint, logger)),
        ("yt_dlp_subtitles", lambda: _fetch_ytdlp_subtitles(config, candidate, language_hint, logger)),
    ]
    if config.enable_asr_fallback:
        providers.append(
            ("asr", lambda: _fetch_asr_transcript(config, candidate, run_dir, state, language_hint, logger))
        )

    attempted: list[str] = []
    for provider_name, loader in providers:
        attempted.append(provider_name)

        cached = store.get_transcript_cache(candidate.video_id, provider_name)
        if cached is not None:
            cached.attempted_providers = attempted.copy()
            if cached.status == "available":
                return cached
            continue

        if store.get_provider_cooldown(provider_name, user_id=user_id):
            # ONEMLI: cooldown durumu ARTIK transkript onbellegine yazilmiyor.
            # Eskiden 15 dakikalik cooldown, 1 saatlik bir "cooldown" onbellek kaydi
            # birakiyor ve saglayici iyilestikten sonra bile atlanmaya devam ediyordu.
            if logger:
                logger.info("Skipping %s for %s: provider in cooldown", provider_name, candidate.video_id)
            continue

        if provider_name == "asr" and not state.claim_asr_slot(config.max_asr_videos_per_run):
            if logger:
                logger.info(
                    "Skipping ASR for %s: run limit of %s reached",
                    candidate.video_id,
                    config.max_asr_videos_per_run,
                )
            continue

        if not state.claim_attempt(candidate.video_id, provider_name):
            continue

        try:
            result = retry_with_backoff(
                loader,
                attempts=config.retry_max_attempts,
                base_delay=config.retry_base_delay_sec,
                logger=logger,
                on_exception=(ProviderTemporaryError,),
            )
        except ProviderRateLimitedError as exc:
            # Sunucu acikca "yavasla" diyor. Tekrar DENEME; saglayiciyi hemen
            # dinlendir. Eskiden bu hata genel "gecici hata" sayiliyor, her video
            # icin 3 kez tekrarlaniyor ve IP blogunu derinlestiriyordu.
            cooldown = exc.retry_after or config.rate_limit_cooldown_sec
            store.mark_provider_cooldown(provider_name, redact_secrets(str(exc)), cooldown, user_id=user_id)
            if logger:
                logger.warning(
                    "Rate limited on %s; cooling down for %ss without retrying", provider_name, cooldown
                )
            # Onbellege YAZMIYORUZ: hiz siniri videoyla ilgili degil, saglayiciyla.
            # Durum `provider_health` tablosunda zaten tutuluyor.
            continue
        except VideoUnavailableError as exc:
            # Videoya OZGU kalici durum: saglayici saglikli, sadece bu video uygun degil.
            result = TranscriptResult(
                video_id=candidate.video_id,
                status="failed_permanent",
                source=provider_name,
                error=redact_secrets(str(exc)),
                attempted_providers=attempted.copy(),
            )
            store.put_transcript_cache(result, config.transcript_cache_ttl_sec)
            continue
        except ProviderPermanentError as exc:
            # Yapilandirma/kurulum hatasi: tekrar denemek fayda etmez, cezalandirmak da.
            if logger:
                logger.error("Permanent transcript failure on %s: %s", provider_name, exc)
            result = TranscriptResult(
                video_id=candidate.video_id,
                status="failed_permanent",
                source=provider_name,
                error=redact_secrets(str(exc)),
                attempted_providers=attempted.copy(),
            )
            store.put_transcript_cache(result, config.failure_cache_ttl_sec)
            continue
        except Exception as exc:
            message = redact_secrets(str(exc))
            failure_count, cooled_down = store.record_provider_failure(
                provider_name,
                message,
                config.provider_cooldown_sec,
                threshold=config.provider_failure_threshold,
                user_id=user_id,
            )
            if logger:
                logger.warning(
                    "Transcript failure %s on %s for %s (cooldown=%s): %s",
                    failure_count,
                    provider_name,
                    candidate.video_id,
                    cooled_down,
                    message,
                )
            result = TranscriptResult(
                video_id=candidate.video_id,
                status="failed_temporary",
                source=provider_name,
                error=message,
                attempted_providers=attempted.copy(),
            )
            store.put_transcript_cache(result, config.failure_cache_ttl_sec)
            continue

        result.attempted_providers = attempted.copy()
        if result.status == "available":
            store.put_transcript_cache(result, config.transcript_cache_ttl_sec)
            store.clear_provider_cooldown(provider_name, user_id=user_id)
            return result

        store.put_transcript_cache(result, config.failure_cache_ttl_sec)
        # Saglayici cevap verdi ama icerik yok: bu bir saglayici arizasi degil.
        store.clear_provider_cooldown(provider_name, user_id=user_id)

    return TranscriptResult(
        video_id=candidate.video_id,
        status="unavailable",
        source="none",
        attempted_providers=attempted,
        error="No transcript provider produced usable content",
    )


def _fetch_youtube_transcript(
    candidate: VideoCandidate, language_hint: str | None, logger=None
) -> TranscriptResult:
    provider = YouTubeTranscriptAPIProvider()
    transcript = provider.fetch(candidate.video_id, language_hint)
    if transcript:
        if logger:
            logger.info("Transcript resolved via %s for %s", provider.name, candidate.video_id)
        return transcript
    return TranscriptResult(video_id=candidate.video_id, status="unavailable", source=provider.name)


def _fetch_ytdlp_subtitles(
    config: AppConfig, candidate: VideoCandidate, language_hint: str | None, logger=None
) -> TranscriptResult:
    provider = YtDlpProvider(config)
    transcript = provider.fetch_subtitles(candidate.url, candidate.video_id, language_hint)
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
    language_hint: str | None,
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
    audio_path = state.downloaded_audio.get(candidate.video_id)
    if not audio_path or not os.path.exists(audio_path):
        audio_path = ytdlp.download_audio(candidate.url, str(audio_dir), candidate.video_id)
        state.downloaded_audio[candidate.video_id] = audio_path

    if logger:
        logger.info("Using ASR backend %s for %s", backend.name, candidate.video_id)
    try:
        result = backend.transcribe(audio_path, candidate.video_id, language_hint)
    finally:
        _cleanup_audio(state, candidate.video_id, audio_path)

    result.source = "asr"
    result.backend = backend.name
    if result.status != "available" and logger:
        logger.warning("ASR backend %s returned %s for %s", backend.name, result.status, candidate.video_id)
    return result


def _cleanup_audio(state: RunTranscriptState, video_id: str, audio_path: str) -> None:
    """Ses dosyasini siler ve state'teki yolu da kaldirir.

    Eskiden dosya siliniyor ama `state.downloaded_audio` silinmis dosyanin yolunu
    tutmaya devam ediyordu; tekrar giris halinde var olmayan dosya kullanilirdi.
    """
    if os.path.exists(audio_path):
        try:
            os.remove(audio_path)
        except OSError:
            pass
    with state.lock:
        state.downloaded_audio.pop(video_id, None)
