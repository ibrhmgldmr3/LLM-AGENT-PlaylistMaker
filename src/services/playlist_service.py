from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from src.config import AppConfig
from src.models import (
    ExportArtifacts,
    PlaylistRequest,
    PlaylistResult,
    ProgressEvent,
    Recommendation,
    SubtopicResult,
    TranscriptResult,
)
from src.providers import GeminiLLMProvider
from src.services.metadata_ranker import rank_candidates
from src.services.playlist_publish_service import create_youtube_playlist
from src.services.recommendation_service import select_recommendation
from src.services.topic_service import generate_subtopics
from src.services.transcript_service import RunTranscriptState, get_transcript
from src.services.youtube_search_service import search_candidates
from src.storage import SQLiteStore
from src.utils.logging_utils import run_log_path, setup_logger


def build_playlist(config: AppConfig, request: PlaylistRequest, progress_callback=None) -> PlaylistResult:
    if os.name == "nt" and config.allow_unsafe_openmp_workaround:
        os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

    run_id = uuid4().hex
    run_dir = config.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    store = SQLiteStore(config.sqlite_path)
    logger = setup_logger("playlist", run_log_path(str(run_dir)))
    store.create_run(run_id, request.topic, request.filters.model_dump())

    def emit(stage: str, message: str, progress: float, current: int | None = None, total: int | None = None) -> None:
        if progress_callback:
            progress_callback(
                ProgressEvent(stage=stage, message=message, progress=progress, current=current, total=total)
            )

    emit("topic_planning", "Generating subtopics", 0.05)
    llm = GeminiLLMProvider(config)
    subtopics = generate_subtopics(llm, request.topic, request.filters.language, logger=logger)

    transcript_state = RunTranscriptState()
    transcript_results_by_video: dict[str, TranscriptResult] = {}
    recommendations: list[Recommendation] = []
    subtopic_results: list[SubtopicResult] = []
    warnings: list[str] = []
    used_video_ids: set[str] = set()

    total_subtopics = len(subtopics) or 1
    for index, subtopic in enumerate(subtopics, start=1):
        query = f"{request.topic} {subtopic.title}"
        emit(
            "candidate_search",
            f"Searching candidates for {subtopic.title}",
            0.1 + ((index - 1) / total_subtopics) * 0.22,
            index,
            total_subtopics,
        )
        candidates = search_candidates(config, store, query, request.filters, logger=logger)

        emit(
            "metadata_ranking",
            f"Ranking metadata for {subtopic.title}",
            0.32 + ((index - 1) / total_subtopics) * 0.22,
            index,
            total_subtopics,
        )
        ranked = rank_candidates(candidates, request.topic, subtopic.title, request.filters)
        shortlisted = ranked[: config.metadata_top_k]

        transcripts: dict[str, TranscriptResult] = {}
        emit(
            "transcript_enrichment",
            f"Enriching top candidates for {subtopic.title}",
            0.54 + ((index - 1) / total_subtopics) * 0.22,
            index,
            total_subtopics,
        )
        for candidate, _metadata_score in shortlisted:
            if candidate.video_id not in transcript_results_by_video:
                transcript_results_by_video[candidate.video_id] = get_transcript(
                    config,
                    store,
                    candidate,
                    str(run_dir),
                    transcript_state,
                    logger=logger,
                )
                store.add_run_video(
                    run_id,
                    "transcript",
                    candidate.video_id,
                    transcript_results_by_video[candidate.video_id].model_dump(),
                )
            transcript = transcript_results_by_video[candidate.video_id]
            transcripts[candidate.video_id] = transcript

        recommendation = select_recommendation(subtopic.title, shortlisted, transcripts, used_video_ids, index)
        if recommendation is None:
            warnings.append(f"No unique recommendation found for subtopic '{subtopic.title}'.")
            subtopic_result = SubtopicResult(
                subtopic=subtopic,
                query=query,
                candidates_considered=len(candidates),
                shortlisted_candidates=[candidate for candidate, _score in shortlisted],
                selected_video_id=None,
                transcript_status="unavailable",
                notes=["No unique candidate survived ranking and deduplication."],
            )
        else:
            recommendations.append(recommendation)
            subtopic_result = SubtopicResult(
                subtopic=subtopic,
                query=query,
                candidates_considered=len(candidates),
                shortlisted_candidates=[candidate for candidate, _score in shortlisted],
                selected_video_id=recommendation.video.video_id,
                transcript_status=recommendation.transcript_status,
                notes=[recommendation.why_selected],
            )
            store.add_run_video(run_id, "recommendation", recommendation.video.video_id, recommendation.model_dump())
        subtopic_results.append(subtopic_result)
        store.add_run_subtopic(run_id, index, subtopic_result.model_dump())

    emit("final_playlist_assembly", "Assembling final playlist", 0.9)
    result = PlaylistResult(
        run_id=run_id,
        topic=request.topic,
        filters=request.filters,
        subtopics=subtopic_results,
        recommendations=recommendations,
        warnings=warnings,
    )

    if request.create_youtube_playlist:
        try:
            result.published_playlist_url = create_youtube_playlist(config, result, logger=logger)
        except Exception as exc:
            warnings.append(f"YouTube playlist creation failed: {exc}")

    exports = export_playlist_artifacts(run_dir, result)
    result.exports = exports

    store.finalize_run(run_id, result)
    emit("done", "Playlist created", 1.0)
    return result


def export_playlist_artifacts(run_dir: Path, result: PlaylistResult) -> ExportArtifacts:
    json_path = run_dir / "result.json"
    markdown_path = run_dir / "study_plan.md"
    json_path.write_text(json.dumps(result.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_render_markdown(result), encoding="utf-8")
    return ExportArtifacts(json_path=str(json_path), markdown_path=str(markdown_path))


def _render_markdown(result: PlaylistResult) -> str:
    lines = [
        f"# Study Plan: {result.topic}",
        "",
        f"Generated at: {result.created_at}",
        "",
        "## Playlist",
        "",
    ]
    for recommendation in result.recommendations:
        lines.extend(
            [
                f"{recommendation.position}. [{recommendation.video.title}]({recommendation.video.url})",
                f"   - Subtopic: {recommendation.subtopic}",
                f"   - Why: {recommendation.why_selected}",
                f"   - Confidence: {recommendation.confidence_score}",
                f"   - Transcript: {recommendation.transcript_status}",
                "",
            ]
        )
    if result.warnings:
        lines.extend(["## Warnings", ""])
        lines.extend([f"- {warning}" for warning in result.warnings])
        lines.append("")
    return "\n".join(lines)
