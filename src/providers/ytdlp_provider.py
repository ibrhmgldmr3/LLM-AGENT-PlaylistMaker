from __future__ import annotations

import glob
import html
import os
import re
from dataclasses import dataclass
from pathlib import Path

import requests
import yt_dlp

from src.config import AppConfig
from src.models import FilterOptions, TranscriptResult, VideoCandidate
from src.providers.errors import ProviderRateLimitedError, ProviderTemporaryError, VideoUnavailableError


def _parse_retry_after(value: str | None) -> int | None:
    """`Retry-After` basligini saniyeye cevirir (saniye bicimi; tarih bicimi yok sayilir)."""
    if not value:
        return None
    try:
        seconds = int(value.strip())
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None
from src.utils.text_utils import normalize_text
from src.utils.ytdlp_options import build_ydl_common_options


MIN_TRANSCRIPT_CHARS = 50

# Altyazi olarak islenmemesi gereken sozde diller.
_NON_SUBTITLE_KEYS = {"live_chat", "rechat"}

# VTT satir ici etiketleri: <00:00:01.000>, <c.colorE5E5E5>, </c>, <v Speaker>
_VTT_TAG_RE = re.compile(r"<[^>]*>")

# Videoya ozgu, kalici yt-dlp hatalarini tanimak icin kullanilan ipuclari.
_VIDEO_LEVEL_HINTS = (
    "private video",
    "video unavailable",
    "removed by the uploader",
    "members-only",
    "this video is not available",
    "sign in to confirm your age",
    "video has been removed",
)


def _classify_ytdlp_error(exc: Exception) -> Exception:
    message = str(exc)
    lowered = message.lower()
    if any(hint in lowered for hint in _VIDEO_LEVEL_HINTS):
        return VideoUnavailableError(message)
    return ProviderTemporaryError(message)


@dataclass
class YtDlpProvider:
    config: AppConfig

    name: str = "yt_dlp"

    def search(self, query: str, filters: FilterOptions, limit: int) -> list[VideoCandidate]:
        options = build_ydl_common_options(self.config)
        # extract_flat: her aday icin tam ayristirma yapmadan hizli liste alir.
        # force_generic_extractor KALDIRILDI: `ytsearch:` sozde-URL'iyle celisiyordu
        # ve generic extractor bu formati isleyemiyordu.
        options["extract_flat"] = True
        options["noplaylist"] = False
        search_url = f"ytsearch{max(1, limit)}:{query}"
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(search_url, download=False)
        except Exception as exc:
            raise _classify_ytdlp_error(exc) from exc

        candidates: list[VideoCandidate] = []
        for entry in (info or {}).get("entries") or []:
            if not entry or not entry.get("id"):
                continue
            candidates.append(
                VideoCandidate(
                    video_id=entry["id"],
                    url=entry.get("url") or f"https://www.youtube.com/watch?v={entry['id']}",
                    title=entry.get("title") or "",
                    description=entry.get("description") or "",
                    channel=entry.get("uploader") or entry.get("channel"),
                    duration_sec=_safe_duration(entry.get("duration")),
                    view_count=entry.get("view_count"),
                    publish_date=entry.get("upload_date"),
                    # ONEMLI: burada eskiden `filters.language` yaziliyordu. Bu, her
                    # yt-dlp adayina kosulsuz "dil eslesti" puani kazandiriyor ve dil
                    # filtresini fiilen etkisiz hale getiriyordu. Gercek dil bilinmiyor.
                    language=entry.get("language"),
                    is_live=bool(entry.get("is_live")),
                    discovery_provider=self.name,
                )
            )
        return candidates

    def fetch_subtitles(self, url: str, video_id: str, language_hint: str | None = None) -> TranscriptResult | None:
        options = build_ydl_common_options(self.config)
        options["skip_download"] = True
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            raise _classify_ytdlp_error(exc) from exc

        info = info or {}
        # Elle girilmis altyazilar otomatik olanlardan daha guvenilir.
        manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k not in _NON_SUBTITLE_KEYS}
        automatic = {k: v for k, v in (info.get("automatic_captions") or {}).items() if k not in _NON_SUBTITLE_KEYS}

        for captions, is_auto in ((manual, False), (automatic, True)):
            selection = _select_caption_track(captions, language_hint)
            if not selection:
                continue
            language, subtitle_url = selection
            try:
                response = requests.get(subtitle_url, timeout=self.config.request_timeout_sec)
            except requests.RequestException as exc:
                raise ProviderTemporaryError(f"Subtitle download failed: {exc}") from exc
            if response.status_code == 429:
                # Hiz sinirinda tekrar denemek IP blogunu uzatir; hemen dinlendir.
                raise ProviderRateLimitedError(
                    "YouTube altyazı indirmede hız sınırı (429)",
                    retry_after=_parse_retry_after(response.headers.get("Retry-After")),
                )
            try:
                response.raise_for_status()
            except requests.RequestException as exc:
                raise ProviderTemporaryError(f"Subtitle download failed: {exc}") from exc
            text = _vtt_to_text(response.text)
            if len(text) < MIN_TRANSCRIPT_CHARS:
                continue
            return TranscriptResult(
                video_id=video_id,
                status="available",
                source="yt_dlp_subtitles",
                language=language,
                text=text,
                backend="automatic" if is_auto else "manual",
            )
        return None

    def download_audio(self, url: str, target_dir: str, video_id: str) -> str:
        """16 kHz mono WAV uretir.

        whisper.cpp yalnizca 16 kHz WAV okuyabiliyor; onceki surum her zaman mp3
        uretiyordu ve whisper.cpp yolu pratikte hep basarisiz oluyordu. WAV ayrica
        mp3 kodlama adimini atladigi icin daha hizli.
        """
        Path(target_dir).mkdir(parents=True, exist_ok=True)
        out_base = os.path.join(target_dir, f"{video_id}.%(ext)s")
        # need_media_formats=True: DASH ses akislarini eleme, yoksa indirilecek format kalmaz.
        options = build_ydl_common_options(self.config, need_media_formats=True)
        options.update(
            {
                "skip_download": False,
                "noplaylist": True,
                "format": "bestaudio/best",
                "outtmpl": out_base,
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
                "postprocessor_args": {"extractaudio": ["-ar", "16000", "-ac", "1"]},
            }
        )
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.extract_info(url, download=True)
        except Exception as exc:
            raise _classify_ytdlp_error(exc) from exc

        audio_path = os.path.join(target_dir, f"{video_id}.wav")
        if os.path.exists(audio_path):
            return audio_path

        # Postprocessor calismadiysa (ornegin ffmpeg yok) uretilen ham dosyayi bul.
        produced = sorted(glob.glob(os.path.join(target_dir, f"{video_id}.*")))
        if produced:
            raise ProviderTemporaryError(
                f"ffmpeg WAV dönüşümü yapılamadı (üretilen dosya: {os.path.basename(produced[0])}). "
                "FFMPEG_PATH ayarını kontrol edin."
            )
        raise ProviderTemporaryError("yt-dlp ses indirmesi hiçbir dosya üretmedi")


def _safe_duration(value) -> int | None:
    try:
        duration = int(float(value))
    except (TypeError, ValueError):
        return None
    return duration if duration > 0 else None


def _select_caption_track(
    captions: dict[str, list[dict]], language_hint: str | None
) -> tuple[str, str] | None:
    if not captions:
        return None
    preferences: list[str] = []
    for language in [language_hint, "en", "tr"]:
        if language and language not in preferences:
            preferences.append(language)

    ordered_languages: list[str] = []
    for preference in preferences:
        for available in captions:
            # "en-US" gibi bolgesel varyantlari da kabul et.
            if available == preference or available.startswith(f"{preference}-"):
                if available not in ordered_languages:
                    ordered_languages.append(available)
    for available in captions:
        if available not in ordered_languages:
            ordered_languages.append(available)

    for language in ordered_languages:
        formats = captions.get(language) or []
        subtitle_url = next(
            (item.get("url") for item in formats if item.get("ext") == "vtt" and item.get("url")),
            None,
        )
        if subtitle_url:
            return language, subtitle_url
    return None


def _vtt_to_text(vtt_text: str) -> str:
    lines: list[str] = []
    seen_recent: list[str] = []
    for raw_line in vtt_text.splitlines():
        # Satir ici zaman damgalarini ve <c>/<v> etiketlerini temizle.
        line = _VTT_TAG_RE.sub("", raw_line)
        line = html.unescape(line).strip()
        if not line or "-->" in line or line.isdigit():
            continue
        if line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE", "STYLE")):
            continue
        # Otomatik altyazilar ayni satiri kayan pencere halinde tekrarlar.
        if line in seen_recent:
            continue
        seen_recent.append(line)
        if len(seen_recent) > 4:
            seen_recent.pop(0)
        lines.append(line)
    return normalize_text(" ".join(lines))
