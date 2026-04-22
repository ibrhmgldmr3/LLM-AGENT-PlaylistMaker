import json
import os
import random
import subprocess
import time
from typing import Optional

import requests
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi

from src.config import AppConfig
from src.models import Transcript, now_iso
from src.services.cache_service import get_transcript_cache_path, load_transcript_cache, save_transcript_cache
from src.utils.retry_utils import retry_with_backoff
from src.utils.text_utils import normalize_text
from src.utils.ytdlp_options import build_ydl_common_options


_YTDLP_COOLDOWN_UNTIL = 0.0


def _vtt_to_text(vtt_text: str) -> str:
    lines = []
    for line in vtt_text.splitlines():
        line = line.strip()
        if not line or "-->" in line or line.isdigit():
            continue
        lines.append(line)
    return normalize_text(" ".join(lines))


def _parse_retry_after_seconds(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return seconds


def _note_rate_limit(cooldown_sec: float, logger=None) -> None:
    global _YTDLP_COOLDOWN_UNTIL
    if cooldown_sec <= 0:
        return
    target = time.time() + cooldown_sec
    if target > _YTDLP_COOLDOWN_UNTIL:
        _YTDLP_COOLDOWN_UNTIL = target
    if logger:
        logger.info("Applied yt-dlp cooldown for %.1fs", cooldown_sec)


def _wait_if_rate_limited(logger=None) -> None:
    remaining = _YTDLP_COOLDOWN_UNTIL - time.time()
    if remaining <= 0:
        return
    if logger:
        logger.info("Waiting %.1fs due to previous YouTube rate-limit", remaining)
    time.sleep(remaining)


def _is_rate_limit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "too many requests" in msg or "rate limit" in msg


def _download_vtt_with_backoff(vtt_url: str, timeout_sec: int, logger=None) -> str:
    # YouTube subtitles endpoint can return 429; respect Retry-After and retry.
    max_attempts = 4
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        "Accept": "text/vtt,text/plain,*/*",
    }
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.get(vtt_url, timeout=timeout_sec, headers=headers)
            resp.raise_for_status()
            return resp.text
        except requests.HTTPError as exc:
            last_exc = exc
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code != 429 or attempt == max_attempts:
                raise
            retry_after = _parse_retry_after_seconds(exc.response.headers.get("Retry-After"))
            delay = retry_after if retry_after is not None else (1.5 * (2 ** (attempt - 1)))
            delay += random.uniform(0, 0.35)
            _note_rate_limit(delay, logger=logger)
            if logger:
                logger.warning("VTT endpoint rate-limited (429). Retrying in %.2fs", delay)
            time.sleep(delay)
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == max_attempts:
                raise
            delay = 0.75 * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
            if logger:
                logger.warning("VTT request failed (attempt %s/%s): %s", attempt, max_attempts, exc)
            time.sleep(delay)
    if last_exc:
        raise last_exc
    raise RuntimeError("VTT download failed")


def _fetch_youtube_transcript(video_id: str, logger=None) -> Optional[Transcript]:
    for lang in ["tr", "en"]:
        try:
            transcript_data = YouTubeTranscriptApi.get_transcript(video_id, languages=[lang])
            text = normalize_text(" ".join([item["text"] for item in transcript_data]))
            if len(text) < 50:
                continue
            return Transcript(
                video_id=video_id,
                language=lang,
                source="youtube_transcript",
                text=text,
                fetched_at=now_iso(),
            )
        except Exception as exc:
            if logger:
                logger.info("YouTube transcript failed for %s (%s): %s", video_id, lang, exc)
    return None


def _fetch_ytdlp_captions(config: AppConfig, url: str, video_id: str, timeout_sec: int, logger=None) -> Optional[Transcript]:
    _wait_if_rate_limited(logger=logger)
    ydl_opts = build_ydl_common_options(config)
    ydl_opts["skip_download"] = True

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        if _is_rate_limit_error(exc):
            _note_rate_limit(config.ytdlp_rate_limit_cooldown_sec, logger=logger)
        raise

    captions = info.get("automatic_captions") or info.get("subtitles") or {}
    if not captions:
        return None

    lang = "tr" if "tr" in captions else ("en" if "en" in captions else next(iter(captions.keys()), None))
    if not lang:
        return None

    formats = captions.get(lang) or []
    vtt_url = None
    for f in formats:
        if f.get("ext") == "vtt" and f.get("url"):
            vtt_url = f["url"]
            break
    if not vtt_url:
        return None

    text = _vtt_to_text(_download_vtt_with_backoff(vtt_url, timeout_sec, logger=logger))
    if len(text) < 50:
        return None
    return Transcript(
        video_id=video_id,
        language=lang,
        source="yt_dlp_captions",
        text=text,
        fetched_at=now_iso(),
    )


def _download_audio(config: AppConfig, url: str, out_base: str, ffmpeg_path: Optional[str], logger=None) -> str:
    _wait_if_rate_limited(logger=logger)
    ydl_opts = build_ydl_common_options(config)
    ydl_opts["format"] = "bestaudio/best"
    ydl_opts["outtmpl"] = out_base
    ydl_opts["postprocessors"] = [
        {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
    ]
    if ffmpeg_path:
        ydl_opts["ffmpeg_location"] = ffmpeg_path

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as exc:
        if _is_rate_limit_error(exc):
            _note_rate_limit(config.ytdlp_rate_limit_cooldown_sec, logger=logger)
        raise

    mp3_path = out_base + ".mp3"
    if not os.path.exists(mp3_path):
        raise RuntimeError("Audio download failed")
    return mp3_path


def _whisper_cpp_transcribe(
    config: AppConfig,
    url: str,
    video_id: str,
    run_dir: str,
    logger=None,
) -> Optional[Transcript]:
    config.ensure_whisper_config()
    os.makedirs(run_dir, exist_ok=True)

    out_base = os.path.join(run_dir, f"audio_{video_id}")
    audio_path = _download_audio(config, url, out_base, config.ffmpeg_path, logger=logger)

    out_txt = os.path.join(run_dir, f"whisper_{video_id}.txt")

    cmd = [
        config.whisper_cpp_cli_path,
        "-m",
        config.whisper_cpp_model_path,
        "-f",
        audio_path,
        "-otxt",
        "-of",
        os.path.splitext(out_txt)[0],
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            if logger:
                logger.warning("whisper.cpp failed: %s", result.stderr)
            return None
        if not os.path.exists(out_txt):
            return None
        with open(out_txt, "r", encoding="utf-8") as f:
            text = normalize_text(f.read())
        if len(text) < 50:
            return None
        return Transcript(
            video_id=video_id,
            language=None,
            source="whisper_cpp",
            text=text,
            fetched_at=now_iso(),
        )
    finally:
        try:
            if os.path.exists(audio_path):
                os.remove(audio_path)
        except Exception:
            pass


def get_transcript(
    config: AppConfig,
    video_id: str,
    url: str,
    run_dir: str,
    logger=None,
) -> Optional[Transcript]:
    cache_path = get_transcript_cache_path(config.data_dir, video_id)
    cached = load_transcript_cache(cache_path)
    if cached:
        if logger:
            logger.info("Transcript cache hit for %s", video_id)
        return cached

    def _try_api() -> Optional[Transcript]:
        return _fetch_youtube_transcript(video_id, logger=logger)

    def _try_captions() -> Optional[Transcript]:
        return _fetch_ytdlp_captions(config, url, video_id, config.request_timeout_sec, logger=logger)

    transcript = None
    try:
        transcript = retry_with_backoff(
            _try_api,
            attempts=config.retry_max_attempts,
            base_delay=config.retry_base_delay_sec,
            logger=logger,
        )
    except Exception as exc:
        if logger:
            logger.warning("Transcript API retries exhausted for %s: %s", video_id, exc)
    if transcript:
        save_transcript_cache(cache_path, transcript)
        return transcript

    transcript = None
    try:
        transcript = retry_with_backoff(
            _try_captions,
            attempts=config.retry_max_attempts,
            base_delay=config.retry_base_delay_sec,
            logger=logger,
        )
    except Exception as exc:
        if logger:
            logger.warning("Caption retries exhausted for %s: %s", video_id, exc)
    if transcript:
        save_transcript_cache(cache_path, transcript)
        return transcript

    transcript = None
    try:
        transcript = _whisper_cpp_transcribe(config, url, video_id, run_dir, logger=logger)
    except Exception as exc:
        if logger:
            logger.warning("Whisper fallback failed for %s: %s", video_id, exc)
    if transcript:
        save_transcript_cache(cache_path, transcript)
        return transcript

    return None
