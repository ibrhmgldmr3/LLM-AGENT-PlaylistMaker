from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import requests
import yt_dlp

from src.config import AppConfig
from src.models import FilterOptions, TranscriptResult, VideoCandidate
from src.utils.text_utils import normalize_text
from src.utils.ytdlp_options import build_ydl_common_options


@dataclass
class YtDlpProvider:
    config: AppConfig

    name: str = "yt_dlp"

    def search(self, query: str, filters: FilterOptions, limit: int) -> list[VideoCandidate]:
        options = build_ydl_common_options(self.config)
        options["extract_flat"] = True
        options["force_generic_extractor"] = True
        search_url = f"ytsearch{limit}:{query}"
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(search_url, download=False)
        candidates: list[VideoCandidate] = []
        for entry in info.get("entries", []) or []:
            if not entry or not entry.get("id"):
                continue
            candidates.append(
                VideoCandidate(
                    video_id=entry["id"],
                    url=f"https://www.youtube.com/watch?v={entry['id']}",
                    title=entry.get("title") or "",
                    description=entry.get("description") or "",
                    channel=entry.get("uploader") or entry.get("channel"),
                    duration_sec=entry.get("duration"),
                    view_count=entry.get("view_count"),
                    publish_date=entry.get("upload_date"),
                    language=filters.language,
                    is_live=bool(entry.get("is_live")),
                    discovery_provider=self.name,
                )
            )
        return candidates

    def fetch_subtitles(self, url: str, video_id: str) -> TranscriptResult | None:
        options = build_ydl_common_options(self.config)
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        captions = info.get("subtitles") or info.get("automatic_captions") or {}
        language = "tr" if "tr" in captions else "en" if "en" in captions else next(iter(captions.keys()), None)
        if not language:
            return None
        formats = captions.get(language) or []
        vtt_url = next((item.get("url") for item in formats if item.get("ext") == "vtt" and item.get("url")), None)
        if not vtt_url:
            return None
        response = requests.get(vtt_url, timeout=self.config.request_timeout_sec)
        response.raise_for_status()
        text = _vtt_to_text(response.text)
        if len(text) < 50:
            return None
        return TranscriptResult(
            video_id=video_id,
            status="available",
            source="yt_dlp_subtitles",
            language=language,
            text=text,
        )

    def download_audio(self, url: str, target_dir: str, video_id: str) -> str:
        Path(target_dir).mkdir(parents=True, exist_ok=True)
        out_base = os.path.join(target_dir, f"{video_id}.%(ext)s")
        options = build_ydl_common_options(self.config)
        options.update(
            {
                "skip_download": False,
                "format": "bestaudio/best",
                "outtmpl": out_base,
                "postprocessors": [
                    {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
                ],
            }
        )
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.extract_info(url, download=True)
        audio_path = os.path.join(target_dir, f"{video_id}.mp3")
        if not os.path.exists(audio_path):
            raise RuntimeError("yt-dlp audio download did not produce an mp3 file")
        return audio_path


def _vtt_to_text(vtt_text: str) -> str:
    lines: list[str] = []
    for raw_line in vtt_text.splitlines():
        line = raw_line.strip()
        if not line or "-->" in line or line.isdigit():
            continue
        lines.append(line)
    return normalize_text(" ".join(lines))
