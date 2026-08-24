from __future__ import annotations

import glob
import html
import json
import os
import re
from xml.etree import ElementTree
from dataclasses import dataclass
from pathlib import Path

import requests
import yt_dlp

from src.config import AppConfig
from src.models import FilterOptions, TranscriptResult, TranscriptSegment, VideoCandidate
from src.providers.errors import (
    ProviderPermanentError,
    ProviderRateLimitedError,
    ProviderTemporaryError,
    VideoUnavailableError,
)


def _parse_retry_after(value: str | None) -> int | None:
    """`Retry-After` basligini saniyeye cevirir (saniye bicimi; tarih bicimi yok sayilir)."""
    if not value:
        return None
    try:
        seconds = int(value.strip())
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None
from src.utils.http_identity import build_session
from src.utils.text_utils import MIN_TRANSCRIPT_CHARS, normalize_text
from src.utils.ytdlp_options import build_ydl_common_options


# Altyazi olarak islenmemesi gereken sozde diller.
_NON_SUBTITLE_KEYS = {"live_chat", "rechat"}

# VTT satir ici etiketleri: <00:00:01.000>, <c.colorE5E5E5>, </c>, <v Speaker>
_VTT_TAG_RE = re.compile(r"<[^>]*>")

# WEBVTT cue basligi: "00:01:02.500 --> 00:01:05.000 align:start position:0%"
_VTT_CUE_RE = re.compile(
    r"^\s*(?P<start>(?:\d+:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?)"
    r"\s*-->\s*"
    r"(?P<end>(?:\d+:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?)"
)

# Yapilandirma kaynakli, tekrar denemekle DUZELMEYECEK hatalar.
# En sik gorulen: Chrome 127+ cerezleri App-Bound Encryption ile sakliyor ve
# yt-dlp cozemiyor (yt-dlp#10927). Bu hata gecici sayilirsa her video icin
# 3 kez tekrar denenip saglayici bosuna cooldown'a aliniyor.
_CONFIG_ERROR_HINTS = (
    "failed to decrypt with dpapi",
    "could not copy chrome cookie database",
    "unsupported browser",
    "no such file or directory: 'cookies",
    "cookies file",
)

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
    if any(hint in lowered for hint in _CONFIG_ERROR_HINTS):
        # Yapilandirma hatasi: tekrar denemek asla duzeltmez ve saglayiciyi
        # bosuna cezalandirir. Kullaniciya ne yapacagini soyle.
        return ProviderPermanentError(
            f"{message.strip()}\n"
            "Tarayıcı çerezleri okunamıyor (Chrome 127+ App-Bound Encryption). "
            "`.env` içinde YTDLP_COOKIES_FROM_BROWSER satırını boşaltın ya da "
            "çerezleri bir dosyaya aktarıp YTDLP_COOKIES_FILE ile verin."
        )
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

        # Altyazi DOSYASI da yt-dlp'nin kullandigi kimlikle cekilmeli. Eskiden
        # burada ciplak bir `requests.get` vardi: metadata proxy/cerez/UA ile
        # gidiyor, asil icerik ise sunucunun kendi IP'sinden ve istemci kimligi
        # olmadan isteniyordu. Imzali altyazi URL'leri istegi yapan istemciye
        # bagli olabildigi icin bu yalnizca tutarsiz degil, kirilgan.
        with build_session(self.config) as session:
            for captions, is_auto in ((manual, False), (automatic, True)):
                selection = _select_caption_track(captions, language_hint)
                if not selection:
                    continue
                language, subtitle_url, subtitle_ext = selection
                try:
                    response = session.get(subtitle_url, timeout=self.config.request_timeout_sec)
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
                segments = _subtitle_to_segments(response.text, subtitle_ext)
                text = normalize_text(" ".join(segment.text for segment in segments))
                if len(text) < MIN_TRANSCRIPT_CHARS:
                    continue
                return TranscriptResult(
                    video_id=video_id,
                    status="available",
                    source="yt_dlp_subtitles",
                    language=language,
                    text=text,
                    backend="automatic" if is_auto else "manual",
                    segments=segments,
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
) -> tuple[str, str, str] | None:
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
        # VTT has the most straightforward parser, but YouTube does not
        # guarantee it for every language.  json3 and srv3 carry the same
        # captions and are preferable to treating a captioned video as empty.
        for extension in ("vtt", "json3", "srv3"):
            subtitle_url = next(
                (
                    item.get("url")
                    for item in formats
                    if item.get("ext") == extension and item.get("url")
                ),
                None,
            )
            if subtitle_url:
                return language, subtitle_url, extension
    return None


def _subtitle_to_segments(content: str, extension: str) -> list[TranscriptSegment]:
    """Parse the caption formats yt-dlp exposes for YouTube.

    Unknown formats intentionally yield no segments.  A malformed subtitle
    document should not be mistaken for a provider outage and the next track
    or provider can still be tried.
    """
    if extension == "vtt":
        return _vtt_to_segments(content)
    if extension == "json3":
        return _json3_to_segments(content)
    if extension == "srv3":
        return _srv3_to_segments(content)
    return []


def _json3_to_segments(content: str) -> list[TranscriptSegment]:
    try:
        events = json.loads(content).get("events", [])
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(events, list):
        return []

    segments: list[TranscriptSegment] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        parts = event.get("segs") or []
        text = "".join(
            part.get("utf8", "") for part in parts if isinstance(part, dict)
        ).replace("\n", " ").strip()
        if not text:
            continue
        try:
            start_sec = max(0.0, float(event.get("tStartMs", 0)) / 1000)
        except (TypeError, ValueError):
            start_sec = 0.0
        try:
            duration_ms = float(event.get("dDurationMs"))
            end_sec = start_sec + duration_ms / 1000 if duration_ms > 0 else None
        except (TypeError, ValueError):
            end_sec = None
        segments.append(TranscriptSegment(start_sec=start_sec, end_sec=end_sec, text=text))
    return _drop_repeated_segments(segments)


def _srv3_to_segments(content: str) -> list[TranscriptSegment]:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return []

    segments: list[TranscriptSegment] = []
    for node in root.findall(".//text"):
        text = normalize_text("".join(node.itertext()))
        if not text:
            continue
        try:
            start_sec = max(0.0, float(node.attrib.get("start", 0)))
        except (TypeError, ValueError):
            start_sec = 0.0
        try:
            duration = float(node.attrib.get("dur"))
            end_sec = start_sec + duration if duration > 0 else None
        except (TypeError, ValueError):
            end_sec = None
        segments.append(TranscriptSegment(start_sec=start_sec, end_sec=end_sec, text=text))
    return _drop_repeated_segments(segments)


def _parse_vtt_timestamp(value: str) -> float | None:
    """`HH:MM:SS.mmm` ya da `MM:SS.mmm` -> saniye."""
    parts = value.replace(",", ".").split(":")
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        return None
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    return max(0.0, seconds)


def _drop_repeated_segments(segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    """Remove recent rolling-window caption repeats without losing timing.

    YouTube's automatic tracks can repeat a phrase after more than four cues;
    retaining a modest twelve-segment window handles those streams while still
    allowing a genuine repeated sentence later in the video.
    """
    kept: list[TranscriptSegment] = []
    recent: list[str] = []
    for segment in segments:
        key = normalize_text(segment.text).casefold()
        if not key or key in recent:
            continue
        kept.append(segment)
        recent.append(key)
        if len(recent) > 12:
            recent.pop(0)
    return kept


def _vtt_to_segments(vtt_text: str) -> list[TranscriptSegment]:
    """VTT'yi zaman damgali segmentlere cevirir.

    Kayan pencere TEKRAR ELEMESI korunuyor: otomatik altyazilar ayni satiri
    ardisik cue'larda yeniden yaziyor ve elenmezse metin ~3 katina cikiyor.
    Elenen tekrar, ILK gorundugu cue'nun zamanina yazilir -- dogru olan o,
    cunku o an ilk soylendigi andir.

    Bir cue'nun birden fazla metin satiri olabilir; hepsi AYNI segmentte
    birlestiriliyor (ayri segmentlere bolmek ayni zaman damgasini tasiyan
    yapay parcalar uretirdi).
    """
    segments: list[TranscriptSegment] = []
    seen_recent: list[str] = []
    start_sec: float | None = None
    end_sec: float | None = None
    pending: list[str] = []

    def flush() -> None:
        nonlocal pending
        if pending and start_sec is not None:
            segments.append(
                TranscriptSegment(
                    start_sec=start_sec,
                    end_sec=end_sec if end_sec is not None and end_sec >= start_sec else None,
                    text=" ".join(pending),
                )
            )
        pending = []

    for raw_line in vtt_text.splitlines():
        cue = _VTT_CUE_RE.match(raw_line)
        if cue:
            flush()
            start_sec = _parse_vtt_timestamp(cue.group("start"))
            end_sec = _parse_vtt_timestamp(cue.group("end"))
            continue

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
        if len(seen_recent) > 12:
            seen_recent.pop(0)
        pending.append(line)

    flush()
    return segments


def _vtt_to_text(vtt_text: str) -> str:
    """VTT'nin duz metin hali. Segmentlerden TURETILIYOR.

    Ayri bir ayristirici DEGIL: iki ayri gecis, birinin degisip digerinin
    degismedigi bir ayrisma noktasi olurdu. Cue basligi hic olmayan (yalnizca
    metin satirlari iceren) bozuk bir VTT'de segment uretilemez; o durumda
    metin de bos doner ve cagiran taraf `MIN_TRANSCRIPT_CHARS` kontroluyle
    zaten bir sonraki saglayiciya geciyor.
    """
    return normalize_text(" ".join(segment.text for segment in _vtt_to_segments(vtt_text)))
