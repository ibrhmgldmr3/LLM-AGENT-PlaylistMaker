from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class VideoCandidate(BaseModel):
    video_id: str
    url: str
    title: str
    channel: Optional[str] = None
    duration_sec: Optional[int] = None
    view_count: Optional[int] = None
    publish_date: Optional[str] = None
    is_live: bool = False
    metadata_score: float = 0.0


class Transcript(BaseModel):
    video_id: str
    language: Optional[str] = None
    source: str
    text: str
    fetched_at: str


class VideoScore(BaseModel):
    kapsam_uyumu: int = Field(..., ge=0, le=10)
    bilgi_derinligi: int = Field(..., ge=0, le=10)
    anlatim_tarzi: int = Field(..., ge=0, le=10)
    hedef_kitle: int = Field(..., ge=0, le=10)
    yapisal_tutarlilik: int = Field(..., ge=0, le=10)
    genel_puan: int = Field(..., ge=0, le=10)
    yorum: str


class SubtopicResult(BaseModel):
    subtopic: str
    candidates_considered: int
    top_scored: int
    selected_video_id: Optional[str]
    scores: List[dict]


class PlaylistResult(BaseModel):
    run_id: str
    topic: str
    subtopics: List[SubtopicResult]
    selected_videos: List[VideoCandidate]
    created_at: str
    warnings: List[str] = []


class ProgressEvent(BaseModel):
    stage: str
    message: str
    progress: float
    current: Optional[int] = None
    total: Optional[int] = None

    @field_validator("progress")
    @classmethod
    def validate_progress(cls, v: float) -> float:
        if v < 0.0:
            return 0.0
        if v > 1.0:
            return 1.0
        return v


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"
