from __future__ import annotations

from dataclasses import dataclass

import youtube_transcript_api as _yta
from youtube_transcript_api import YouTubeTranscriptApi

from src.models import TranscriptResult
from src.providers.errors import ProviderRateLimitedError, ProviderTemporaryError, VideoUnavailableError
from src.utils.text_utils import normalize_text


MIN_TRANSCRIPT_CHARS = 50


def _exc(*names: str) -> tuple[type[BaseException], ...]:
    """Kutuphane surumleri arasinda degisen istisna adlarini guvenle toplar."""
    found = tuple(
        getattr(_yta, name) for name in names if isinstance(getattr(_yta, name, None), type)
    )
    return found or (LookupError,)


# Videoya ozgu, kalici durumlar -> saglayici cezalandirilmamali.
_VIDEO_LEVEL_ERRORS = _exc(
    "TranscriptsDisabled",
    "NoTranscriptFound",
    "VideoUnavailable",
    "VideoUnplayable",
    "NotTranslatable",
    "TranslationLanguageNotAvailable",
    "AgeRestricted",
)

# Hiz siniri / IP blogu -> tekrar denemek DURUMU KOTULESTIRIR.
_RATE_LIMIT_ERRORS = _exc(
    "RequestBlocked",
    "IpBlocked",
    "TooManyRequests",
)

# Diger altyapi hatalari -> tekrar denenebilir.
_INFRA_ERRORS = _exc(
    "YouTubeRequestFailed",
)


@dataclass
class YouTubeTranscriptAPIProvider:
    name: str = "youtube_transcript_api"

    def _preferred_languages(self, language_hint: str | None) -> list[str]:
        languages: list[str] = []
        for language in [language_hint, "en", "tr"]:
            if language and language not in languages:
                languages.append(language)
        return languages

    def fetch(self, video_id: str, language_hint: str | None = None) -> TranscriptResult | None:
        """Tek cagrida tercih sirasina gore altyazi ceker.

        Onceki surum kaldirilmis olan `YouTubeTranscriptApi.get_transcript` statik
        metodunu cagiriyordu (1.x'te AttributeError). Ayrica dilleri tek tek
        deniyordu ve ilk dildeki istisna donguyu kiriyordu.
        """
        languages = self._preferred_languages(language_hint)
        try:
            snippets, language_code = self._fetch_raw(video_id, languages)
        except _VIDEO_LEVEL_ERRORS as exc:
            # Bu videoda altyazi yok/kapali: saglayici saglikli, sadece video uygun degil.
            raise VideoUnavailableError(str(exc)) from exc
        except _RATE_LIMIT_ERRORS as exc:
            # IP blogu: tekrar denemek YouTube'un gozunde durumu kotulestirir.
            raise ProviderRateLimitedError(str(exc)) from exc
        except _INFRA_ERRORS as exc:
            raise ProviderTemporaryError(str(exc)) from exc

        text = normalize_text(" ".join(snippets))
        if len(text) < MIN_TRANSCRIPT_CHARS:
            return None
        return TranscriptResult(
            video_id=video_id,
            status="available",
            source=self.name,
            language=language_code,
            text=text,
        )

    def _fetch_raw(self, video_id: str, languages: list[str]) -> tuple[list[str], str | None]:
        """1.x (`fetch`) ve 0.6.x (`get_transcript`) API'lerinin ikisini de destekler."""
        api_instance_fetch = getattr(YouTubeTranscriptApi, "fetch", None)
        if callable(api_instance_fetch):
            fetched = YouTubeTranscriptApi().fetch(video_id, languages=languages)
            language_code = getattr(fetched, "language_code", None)
            snippets = [getattr(snippet, "text", "") or "" for snippet in fetched]
            return snippets, language_code

        legacy_get = getattr(YouTubeTranscriptApi, "get_transcript", None)
        if callable(legacy_get):
            rows = legacy_get(video_id, languages=languages)
            snippets = [row.get("text", "") for row in rows]
            return snippets, languages[0] if languages else None

        raise ProviderTemporaryError(
            "youtube-transcript-api sürümü desteklenmiyor: ne `fetch` ne de `get_transcript` bulundu"
        )
