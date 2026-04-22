from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from src.config import AppConfig
from src.models import FilterOptions, VideoCandidate


class ProviderTemporaryError(RuntimeError):
    pass


class ProviderPermanentError(Exception):
    pass


@dataclass
class YouTubeDataAPIProvider:
    config: AppConfig

    name: str = "youtube_data_api"

    def is_configured(self) -> bool:
        return bool(self.config.youtube_data_api_key)

    def search(self, query: str, filters: FilterOptions, limit: int) -> list[VideoCandidate]:
        if not self.is_configured():
            raise ProviderPermanentError("YOUTUBE_DATA_API_KEY is not configured")

        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": min(limit, 20),
            "key": self.config.youtube_data_api_key,
            "relevanceLanguage": filters.language,
            "safeSearch": "moderate",
        }
        try:
            search_resp = requests.get(
                "https://www.googleapis.com/youtube/v3/search",
                params=params,
                timeout=self.config.request_timeout_sec,
            )
            if search_resp.status_code in {403, 429, 500, 503}:
                raise ProviderTemporaryError(f"YouTube Data API search failed: {search_resp.status_code}")
            search_resp.raise_for_status()
            search_data = search_resp.json()
        except requests.RequestException as exc:
            raise ProviderTemporaryError(f"YouTube Data API search request failed: {exc}") from exc

        video_ids = [item["id"]["videoId"] for item in search_data.get("items", []) if item.get("id", {}).get("videoId")]
        if not video_ids:
            return []
        details = self._fetch_video_details(video_ids)
        detail_map = {item["id"]: item for item in details}
        candidates: list[VideoCandidate] = []
        for item in search_data.get("items", []):
            video_id = item.get("id", {}).get("videoId")
            if not video_id or video_id not in detail_map:
                continue
            snippet = item.get("snippet", {})
            detail = detail_map[video_id]
            content_details = detail.get("contentDetails", {})
            statistics = detail.get("statistics", {})
            snippet_detail = detail.get("snippet", {})
            candidates.append(
                VideoCandidate(
                    video_id=video_id,
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    title=snippet.get("title", ""),
                    description=snippet.get("description", ""),
                    channel=snippet.get("channelTitle"),
                    duration_sec=_parse_iso_duration_seconds(content_details.get("duration")),
                    view_count=_safe_int(statistics.get("viewCount")),
                    publish_date=snippet_detail.get("publishedAt") or snippet.get("publishedAt"),
                    language=snippet_detail.get("defaultAudioLanguage") or snippet_detail.get("defaultLanguage"),
                    is_live=snippet.get("liveBroadcastContent") == "live",
                    discovery_provider=self.name,
                )
            )
        return candidates

    def _fetch_video_details(self, video_ids: list[str]) -> list[dict[str, Any]]:
        params = {
            "part": "contentDetails,statistics,snippet",
            "id": ",".join(video_ids),
            "key": self.config.youtube_data_api_key,
            "maxResults": len(video_ids),
        }
        try:
            response = requests.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params=params,
                timeout=self.config.request_timeout_sec,
            )
            if response.status_code in {403, 429, 500, 503}:
                raise ProviderTemporaryError(f"YouTube Data API video details failed: {response.status_code}")
            response.raise_for_status()
            return response.json().get("items", [])
        except requests.RequestException as exc:
            raise ProviderTemporaryError(f"YouTube Data API video details request failed: {exc}") from exc


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_duration_seconds(value: str | None) -> int | None:
    if not value or not value.startswith("PT"):
        return None
    hours = minutes = seconds = 0
    current = ""
    for char in value[2:]:
        if char.isdigit():
            current += char
            continue
        if char == "H":
            hours = int(current or "0")
        elif char == "M":
            minutes = int(current or "0")
        elif char == "S":
            seconds = int(current or "0")
        current = ""
    return hours * 3600 + minutes * 60 + seconds
