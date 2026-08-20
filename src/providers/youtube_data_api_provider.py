from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import Any, Callable

import requests
from requests.adapters import HTTPAdapter

from src.config import AppConfig
from src.models import FilterOptions, VideoCandidate
from src.providers.errors import (
    ProviderPermanentError,
    ProviderRateLimitedError,
    ProviderTemporaryError,
)
from src.utils.logging_utils import redact_secrets


SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

# Kota maliyetleri (birim/cagri). Google'in resmi listesi; `search.list`
# digerlerinden 100 KAT pahali ve gunluk kotanin pratikte tamamini o yiyor.
QUOTA_UNITS: dict[str, int] = {
    SEARCH_URL: 100,
    VIDEOS_URL: 1,
    CHANNELS_URL: 1,
}

# Raporlamada kullanilan uc adlari (URL yerine okunabilir etiket).
ENDPOINT_NAMES: dict[str, str] = {
    SEARCH_URL: "search.list",
    VIDEOS_URL: "videos.list",
    CHANNELS_URL: "channels.list",
}

# Varsayilan gunluk proje kotasi. Google Cloud Console'dan arttirilabilir;
# arttirildiysa `YOUTUBE_DAILY_QUOTA_UNITS` ile bildirilir.
DEFAULT_DAILY_QUOTA_UNITS = 10_000

# YouTube Data API sinirlari
MAX_SEARCH_RESULTS = 50
MAX_VIDEO_IDS_PER_CALL = 50
MAX_CHANNEL_IDS_PER_CALL = 50

# Tekrar denemenin fayda etmeyecegi hata nedenleri.
_PERMANENT_REASONS = {
    "keyinvalid",
    "keyexpired",
    "accessnotconfigured",
    "ipreferrerblocked",
    "forbidden",
    "badrequest",
    "invalidparameter",
}
# Gecici, tekrar denenebilir hata nedenleri.
_TEMPORARY_REASONS = {
    "quotaexceeded",
    "ratelimitexceeded",
    "userratelimitexceeded",
    "backenderror",
    "internalerror",
    "serviceunavailable",
}

@functools.lru_cache(maxsize=1)
def _session() -> requests.Session:
    """Paylasimli HTTP oturumu.

    Aramalar paralel calistigi icin her istekte yeni TCP+TLS el sikismasi yapmak
    ciddi gecikme ekliyordu. Baglanti havuzu bunu tek sefere indiriyor.
    """
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=4, pool_maxsize=16, max_retries=0)
    session.mount("https://", adapter)
    return session


_ISO_DURATION_RE = re.compile(
    r"^P(?:(?P<weeks>\d+)W)?(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)


@dataclass
class YouTubeDataAPIProvider:
    config: AppConfig

    name: str = "youtube_data_api"

    # Her BASARILI API cagrisinda `(uc_adi, kota_birimi)` ile cagrilir.
    #
    # Geri cagri olarak alinmasinin sebebi: sayaci yazacak olan `SQLiteStore` ve
    # cagriyi yapan kullanicinin kimligi bu katmanda YOK ve buraya tasinmasi
    # saglayiciyi depolama katmanina baglardi. Cagiran taraf (arama servisi)
    # ikisine de sahip, dolayisiyla baglanti orada kuruluyor.
    usage_recorder: Callable[[str, int], None] | None = None

    def is_configured(self) -> bool:
        return bool(self.config.youtube_data_api_key)

    def search(self, query: str, filters: FilterOptions, limit: int) -> list[VideoCandidate]:
        if not self.is_configured():
            raise ProviderPermanentError("YOUTUBE_DATA_API_KEY is not configured")

        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": max(1, min(limit, MAX_SEARCH_RESULTS)),
            "key": self.config.youtube_data_api_key,
            "relevanceLanguage": filters.language,
            "safeSearch": "moderate",
        }
        search_data = self._get(SEARCH_URL, params, "search")

        video_ids = [
            item["id"]["videoId"]
            for item in search_data.get("items", [])
            if item.get("id", {}).get("videoId")
        ]
        if not video_ids:
            return []

        detail_map = {item["id"]: item for item in self._fetch_video_details(video_ids)}

        channel_ids = {
            detail.get("snippet", {}).get("channelId")
            for detail in detail_map.values()
            if detail.get("snippet", {}).get("channelId")
        }
        # channels.list cagrisi sadece 1 kota birimi (search.list 100 birim).
        # Gercek otorite sinyali icin bu maliyet ihmal edilebilir.
        channel_map = self._fetch_channel_stats(sorted(channel_ids))

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
            live_content = snippet_detail.get("liveBroadcastContent") or snippet.get("liveBroadcastContent")
            channel_id = snippet_detail.get("channelId") or snippet.get("channelId")
            channel_stats = channel_map.get(channel_id, {})
            candidates.append(
                VideoCandidate(
                    video_id=video_id,
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    title=snippet_detail.get("title") or snippet.get("title", ""),
                    # Arama sonucundaki aciklama kirpilmis; detay yanitindaki tam metni tercih et.
                    description=snippet_detail.get("description") or snippet.get("description", ""),
                    channel=snippet_detail.get("channelTitle") or snippet.get("channelTitle"),
                    channel_id=channel_id,
                    subscriber_count=_safe_int(channel_stats.get("subscriberCount")),
                    channel_video_count=_safe_int(channel_stats.get("videoCount")),
                    duration_sec=_parse_iso_duration_seconds(content_details.get("duration")),
                    view_count=_safe_int(statistics.get("viewCount")),
                    publish_date=snippet_detail.get("publishedAt") or snippet.get("publishedAt"),
                    language=snippet_detail.get("defaultAudioLanguage") or snippet_detail.get("defaultLanguage"),
                    is_live=live_content in {"live", "upcoming"},
                    discovery_provider=self.name,
                )
            )
        return candidates

    def _fetch_channel_stats(self, channel_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Kanal istatistiklerini toplu ceker. Basarisiz olursa siralama devam eder."""
        stats: dict[str, dict[str, Any]] = {}
        for start in range(0, len(channel_ids), MAX_CHANNEL_IDS_PER_CALL):
            chunk = channel_ids[start : start + MAX_CHANNEL_IDS_PER_CALL]
            params = {
                "part": "statistics",
                "id": ",".join(chunk),
                "key": self.config.youtube_data_api_key,
                "maxResults": len(chunk),
            }
            try:
                payload = self._get(CHANNELS_URL, params, "channel stats")
            except Exception:
                # Otorite sinyali "olsa iyi olur" seviyesinde; yoksa isim ipuclarina donulur.
                return stats
            for item in payload.get("items", []):
                if item.get("id"):
                    stats[item["id"]] = item.get("statistics", {})
        return stats

    def _fetch_video_details(self, video_ids: list[str]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for start in range(0, len(video_ids), MAX_VIDEO_IDS_PER_CALL):
            chunk = video_ids[start : start + MAX_VIDEO_IDS_PER_CALL]
            params = {
                "part": "contentDetails,statistics,snippet",
                "id": ",".join(chunk),
                "key": self.config.youtube_data_api_key,
                "maxResults": len(chunk),
            }
            payload = self._get(VIDEOS_URL, params, "video details")
            items.extend(payload.get("items", []))
        return items

    def _get(self, url: str, params: dict[str, Any], label: str) -> dict[str, Any]:
        try:
            response = _session().get(url, params=params, timeout=self.config.request_timeout_sec)
        except requests.RequestException as exc:
            # Istisna metni tam URL'i (ve API anahtarini) icerebilir.
            raise ProviderTemporaryError(
                redact_secrets(f"YouTube Data API {label} request failed: {exc}")
            ) from exc

        if response.status_code >= 400:
            raise self._classify_error(response, label)

        # Kota yalnizca BASARILI yanitlarda sayiliyor. Kotasi dolmus (403
        # quotaExceeded) bir istek Google tarafindan da ucretlendirilmiyor;
        # 4xx'leri saymak, dolan kotayi bir de biz sisirmek olurdu.
        self._record_usage(url)

        try:
            return response.json()
        except ValueError as exc:
            raise ProviderTemporaryError(f"YouTube Data API {label} returned invalid JSON") from exc

    def _record_usage(self, url: str) -> None:
        """Tuketimi bildirir; olcum hatasi ISI dusurmemeli."""
        if self.usage_recorder is None:
            return
        try:
            self.usage_recorder(ENDPOINT_NAMES.get(url, url), QUOTA_UNITS.get(url, 0))
        except Exception:
            # Sayac yazilamadi diye arama basarisiz sayilmaz: olcum, islevin
            # kendisinden daha az onemli.
            pass

    def _classify_error(self, response: requests.Response, label: str) -> Exception:
        """HTTP durumunu ve API'nin `reason` alanini kullanarak kalici/gecici ayrimi yapar.

        403 hem "kota doldu" (gecici) hem "gecersiz anahtar" (kalici) icin donuyor;
        eskiden ikisi de gecici sayilip bosuna 3 kez tekrar deneniyordu.
        """
        reason = ""
        message = ""
        try:
            payload = response.json().get("error", {})
            message = payload.get("message", "") or ""
            errors = payload.get("errors") or []
            if errors:
                reason = (errors[0].get("reason") or "").lower()
        except Exception:
            pass

        detail = redact_secrets(f"{response.status_code} {reason or message}".strip())
        text = f"YouTube Data API {label} failed: {detail}"

        # Hiz siniri: tekrar denemek yerine saglayiciyi dinlendir.
        if response.status_code == 429 or reason in {"ratelimitexceeded", "userratelimitexceeded"}:
            retry_after = response.headers.get("Retry-After") if hasattr(response, "headers") else None
            try:
                retry_after = int(retry_after) if retry_after else None
            except (TypeError, ValueError):
                retry_after = None
            return ProviderRateLimitedError(text, retry_after=retry_after)
        if reason in _PERMANENT_REASONS:
            return ProviderPermanentError(text)
        if reason in _TEMPORARY_REASONS:
            return ProviderTemporaryError(text)
        if response.status_code in {500, 502, 503, 504}:
            return ProviderTemporaryError(text)
        if response.status_code in {400, 401, 403, 404}:
            return ProviderPermanentError(text)
        return ProviderTemporaryError(text)


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_duration_seconds(value: str | None) -> int | None:
    """ISO 8601 sure ifadesini saniyeye cevirir.

    Onceki elle yazilmis ayristirici hafta/gun bilesenlerini goz ardi ediyordu:
    `P1DT2H` -> None donuyor ve 26 saatlik video "suresi bilinmiyor" sayiliyordu.
    """
    if not value:
        return None
    match = _ISO_DURATION_RE.match(value.strip())
    if not match:
        return None
    parts = match.groupdict()
    if not any(parts.values()):
        return None
    weeks = int(parts["weeks"] or 0)
    days = int(parts["days"] or 0)
    hours = int(parts["hours"] or 0)
    minutes = int(parts["minutes"] or 0)
    seconds = float(parts["seconds"] or 0)
    total = weeks * 604800 + days * 86400 + hours * 3600 + minutes * 60 + seconds
    return int(total)
