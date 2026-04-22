from __future__ import annotations

from dataclasses import dataclass

from youtube_transcript_api import YouTubeTranscriptApi

from src.models import TranscriptResult
from src.utils.text_utils import normalize_text


@dataclass
class YouTubeTranscriptAPIProvider:
    name: str = "youtube_transcript_api"

    def fetch(self, video_id: str, language_hint: str | None = None) -> TranscriptResult | None:
        languages = [language_hint] if language_hint else []
        languages.extend(["en", "tr"])
        tried: list[str] = []
        for language in languages:
            if not language or language in tried:
                continue
            tried.append(language)
            transcript_data = YouTubeTranscriptApi.get_transcript(video_id, languages=[language])
            text = normalize_text(" ".join(item.get("text", "") for item in transcript_data))
            if len(text) < 50:
                continue
            return TranscriptResult(
                video_id=video_id,
                status="available",
                source=self.name,
                language=language,
                text=text,
            )
        return None
