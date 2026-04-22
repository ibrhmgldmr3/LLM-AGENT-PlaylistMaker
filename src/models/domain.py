from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator


TranscriptStatus = Literal["available", "unavailable", "cooldown", "failed_temporary", "failed_permanent"]
DifficultyLevel = Literal["beginner", "intermediate", "advanced", "mixed"]
FreshnessPreference = Literal["balanced", "evergreen", "recent"]


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class FilterOptions(BaseModel):
    language: str = Field(default="en")
    difficulty: DifficultyLevel = Field(default="mixed")
    max_duration_minutes: int = Field(default=60)
    freshness_preference: FreshnessPreference = Field(default="balanced")

    @field_validator("max_duration_minutes")
    @classmethod
    def validate_duration(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("max_duration_minutes must be greater than 0")
        return value


class PlaylistRequest(BaseModel):
    topic: str
    filters: FilterOptions = Field(default_factory=FilterOptions)
    create_youtube_playlist: bool = False


class Subtopic(BaseModel):
    title: str
    normalized_title: str
    source_titles: list[str] = Field(default_factory=list)


class VideoCandidate(BaseModel):
    video_id: str
    url: str
    title: str
    description: str = ""
    channel: str | None = None
    duration_sec: int | None = None
    view_count: int | None = None
    publish_date: str | None = None
    language: str | None = None
    is_live: bool = False
    metadata_score: float = 0.0
    discovery_provider: str | None = None
    channel_quality_score: float = 0.0


class MetadataScore(BaseModel):
    total: float
    title_relevance: float
    description_relevance: float
    channel_quality: float
    duration_fit: float
    difficulty_fit: float = 0.0
    language_match: float
    freshness: float
    engagement: float
    rationale: list[str] = Field(default_factory=list)


class TranscriptResult(BaseModel):
    video_id: str
    status: TranscriptStatus
    source: str
    language: str | None = None
    text: str | None = None
    backend: str | None = None
    attempted_providers: list[str] = Field(default_factory=list)
    error: str | None = None
    fetched_at: str = Field(default_factory=now_utc_iso)


class Recommendation(BaseModel):
    position: int
    subtopic: str
    video: VideoCandidate
    why_selected: str
    confidence_score: float
    transcript_status: TranscriptStatus
    transcript_source: str | None = None
    transcript_backend: str | None = None
    metadata_score: MetadataScore


class SubtopicResult(BaseModel):
    subtopic: Subtopic
    query: str
    candidates_considered: int
    shortlisted_candidates: list[VideoCandidate] = Field(default_factory=list)
    selected_video_id: str | None = None
    transcript_status: TranscriptStatus = "unavailable"
    notes: list[str] = Field(default_factory=list)


class ExportArtifacts(BaseModel):
    json_path: str
    markdown_path: str


class PlaylistResult(BaseModel):
    run_id: str
    topic: str
    filters: FilterOptions
    subtopics: list[SubtopicResult]
    recommendations: list[Recommendation]
    created_at: str = Field(default_factory=now_utc_iso)
    warnings: list[str] = Field(default_factory=list)
    exports: ExportArtifacts | None = None
    published_playlist_url: str | None = None


class ProgressEvent(BaseModel):
    stage: str
    message: str
    progress: float
    current: int | None = None
    total: int | None = None

    @field_validator("progress")
    @classmethod
    def normalize_progress(cls, value: float) -> float:
        if value < 0:
            return 0.0
        if value > 1:
            return 1.0
        return value
