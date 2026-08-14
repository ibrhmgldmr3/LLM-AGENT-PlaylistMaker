"""API istek/yanit semalari.

Alan modelleri (`src/models`) dogrudan kullanilmiyor: API sozlesmesi ile ic
model ayri evrimlesebilmeli. Yanitlarda alan modelleri gomulu olarak donuyor
(pydantic oldugu icin bedava), ama ISTEK semalari acikca burada tanimli.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.models import FilterOptions, PlaylistResult


# `JobState` degerlerinin tel uzerindeki karsiligi. Eskiden bu alanlar duz `str`
# idi; bu bir davranis degil, kesinlik eksikligiydi -- API yalnizca bu bes degeri
# donuyor. Literal yazmak OpenAPI'ye enum olarak yansiyor ve uretilen TypeScript
# tipi de daralarak istemcide `switch` kapsama kontrolunu mumkun kiliyor.
RunState = Literal["pending", "running", "done", "failed", "cancelled"]


class RunOptionsOverride(BaseModel):
    """Calistirma basina ezilebilen ayarlar. Verilmeyenler sunucu varsayilaninda kalir."""

    gemini_model: str | None = None
    max_subtopics: int | None = Field(default=None, ge=1, le=20)
    search_candidates_per_subtopic: int | None = Field(default=None, ge=10, le=50)
    metadata_top_k: int | None = Field(default=None, ge=1, le=10)
    transcript_enrichment_top_k: int | None = Field(default=None, ge=1)
    enable_asr_fallback: bool | None = None
    max_asr_videos_per_run: int | None = Field(default=None, ge=0)
    channel_repeat_penalty: float | None = Field(default=None, ge=0)
    youtube_playlist_privacy_status: Literal["private", "unlisted", "public"] | None = None


class CreateRunRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=300)
    filters: FilterOptions = Field(default_factory=FilterOptions)
    options: RunOptionsOverride = Field(default_factory=RunOptionsOverride)
    create_youtube_playlist: bool = False


class RunAccepted(BaseModel):
    """202 yaniti: is kuyruga alindi, kimlik hazir."""

    run_id: str
    state: RunState
    events_url: str
    result_url: str


class RunSnapshotBody(BaseModel):
    """SSE `done` olayinin govdesi.

    REST yaniti DEGIL, bu yuzden FastAPI onu kendiliginden OpenAPI'ye koymuyor;
    `scripts/dump_openapi.py` acikca ekliyor. Boyle bir modele bagli olmasinin
    sebebi: govde eskiden dogrudan `JobHandle.snapshot()` sozlugu olarak
    yollaniyordu ve is katmaninin IC alan adlari (`job_id`) tel uzerine
    siziyordu. Frontend `run_id` bekledigi icin o alan calisma zamaninda
    `undefined` kaliyordu; TypeScript ise `string` oldugunu iddia ediyordu.
    """

    run_id: str
    state: RunState
    created_at: str
    progress: float = 0.0
    stage: str | None = None
    message: str | None = None
    error: str | None = None


class RunStatus(BaseModel):
    run_id: str
    state: RunState
    progress: float = 0.0
    stage: str | None = None
    message: str | None = None
    error: str | None = None


class RunSummary(BaseModel):
    run_id: str
    topic: str
    created_at: str
    is_complete: bool
    filters: dict[str, Any] = Field(default_factory=dict)


class RunListResponse(BaseModel):
    items: list[RunSummary]
    total: int
    limit: int
    offset: int


class RunResultResponse(BaseModel):
    run_id: str
    state: RunState
    result: PlaylistResult | None = None


class PublishResponse(BaseModel):
    url: str
    added: int
    warnings: list[str] = Field(default_factory=list)


class CapabilitiesResponse(BaseModel):
    """Arayuzun hangi kontrolleri acabilecegi. SIR ICERMEZ."""

    gemini_configured: bool
    youtube_search_configured: bool
    youtube_publish_configured: bool
    asr_available: bool
    cookies_configured: bool
    defaults: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    detail: str
    code: str | None = None
