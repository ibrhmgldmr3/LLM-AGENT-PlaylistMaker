from typing import List
import yt_dlp

from src.config import AppConfig
from src.models import VideoCandidate
from src.utils.retry_utils import retry_with_backoff
from src.utils.youtube_utils import duration_score, title_relevance_score
from src.utils.ytdlp_options import build_ydl_common_options


def _ydl_options(config: AppConfig):
    options = build_ydl_common_options(config)
    options["extract_flat"] = True
    options["skip_download"] = True
    options["force_generic_extractor"] = True
    return options


def search_candidates(config: AppConfig, query: str, logger=None) -> List[VideoCandidate]:
    search_url = f"ytsearch{config.youtube_search_per_topic}:{query}"

    def _call() -> List[VideoCandidate]:
        with yt_dlp.YoutubeDL(_ydl_options(config)) as ydl:
            info = ydl.extract_info(search_url, download=False)
        entries = info.get("entries", []) if info else []
        results: List[VideoCandidate] = []
        for entry in entries:
            if not entry or "id" not in entry:
                continue
            video_id = entry.get("id")
            title = entry.get("title") or ""
            url = f"https://www.youtube.com/watch?v={video_id}"
            duration = entry.get("duration")
            view_count = entry.get("view_count")
            channel = entry.get("uploader") or entry.get("channel")
            publish_date = entry.get("upload_date")
            is_live = bool(entry.get("is_live"))

            results.append(
                VideoCandidate(
                    video_id=video_id,
                    url=url,
                    title=title,
                    channel=channel,
                    duration_sec=duration,
                    view_count=view_count,
                    publish_date=publish_date,
                    is_live=is_live,
                    metadata_score=0.0,
                )
            )
        return results

    return retry_with_backoff(
        _call,
        attempts=config.retry_max_attempts,
        base_delay=config.retry_base_delay_sec,
        logger=logger,
    )


def metadata_prefilter(candidates: List[VideoCandidate], keywords: List[str], top_k: int) -> List[VideoCandidate]:
    for c in candidates:
        score = 0.0
        score += duration_score(c.duration_sec)
        score += title_relevance_score(c.title, keywords)
        if c.is_live:
            score -= 1.0
        c.metadata_score = score
    candidates.sort(key=lambda x: x.metadata_score, reverse=True)
    return candidates[:top_k]
