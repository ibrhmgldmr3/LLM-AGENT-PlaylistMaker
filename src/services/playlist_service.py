from typing import Callable, List, Optional

from src.config import AppConfig
from src.models import PlaylistResult, ProgressEvent, SubtopicResult, VideoCandidate, now_iso
from src.services.cache_service import generate_run_id, get_run_dir, save_playlist_result
from src.services.topic_service import generate_subtopics
from src.services.youtube_search_service import metadata_prefilter, search_candidates
from src.services.transcript_service import get_transcript
from src.services.ranking_service import score_transcript
from src.utils.logging_utils import setup_logger, run_log_path
from src.utils.text_utils import normalize_text


ProgressCallback = Callable[[ProgressEvent], None]


def _emit(callback: Optional[ProgressCallback], event: ProgressEvent) -> None:
    if callback:
        callback(event)


def build_playlist(config: AppConfig, topic: str, progress_callback: Optional[ProgressCallback] = None) -> PlaylistResult:
    run_id = generate_run_id()
    run_dir = get_run_dir(config.data_dir, run_id)
    logger = setup_logger("playlist", run_log_path(run_dir))

    warnings: List[str] = []

    _emit(progress_callback, ProgressEvent(stage="topic", message="Generating subtopics", progress=0.05))
    subtopics = generate_subtopics(config, topic, logger=logger)

    results: List[SubtopicResult] = []
    selected_videos: List[VideoCandidate] = []
    used_ids = set()

    total = len(subtopics)
    for idx, subtopic in enumerate(subtopics, start=1):
        _emit(
            progress_callback,
            ProgressEvent(
                stage="search",
                message=f"Searching videos for: {subtopic}",
                progress=min(0.1 + (idx - 1) / max(total, 1) * 0.6, 0.7),
                current=idx,
                total=total,
            ),
        )
        query = f"{topic} {subtopic}"
        try:
            candidates = search_candidates(config, query, logger=logger)
        except Exception as exc:
            warnings.append(f"Search failed for subtopic '{subtopic}': {exc}")
            logger.warning("Search failed for subtopic %s: %s", subtopic, exc)
            results.append(
                SubtopicResult(
                    subtopic=subtopic,
                    candidates_considered=0,
                    top_scored=0,
                    selected_video_id=None,
                    scores=[],
                )
            )
            continue

        if not candidates:
            warnings.append(f"No candidates found for subtopic: {subtopic}")
            results.append(
                SubtopicResult(
                    subtopic=subtopic,
                    candidates_considered=0,
                    top_scored=0,
                    selected_video_id=None,
                    scores=[],
                )
            )
            continue

        keywords = [w for w in normalize_text(subtopic).split(" ") if len(w) > 2]
        top_candidates = metadata_prefilter(candidates, keywords, config.transcript_score_top_k)

        scores = []
        for candidate in top_candidates:
            _emit(
                progress_callback,
                ProgressEvent(
                    stage="transcript",
                    message=f"Fetching transcript for {candidate.title}",
                    progress=min(0.7, 0.1 + (idx - 1) / max(total, 1) * 0.6),
                    current=idx,
                    total=total,
                ),
            )
            transcript = get_transcript(config, candidate.video_id, candidate.url, run_dir, logger=logger)
            if not transcript:
                logger.info("Transcript missing for %s", candidate.video_id)
                continue
            try:
                score = score_transcript(config, transcript.text, subtopic, logger=logger)
            except Exception as exc:
                logger.warning("Scoring failed for %s: %s", candidate.video_id, exc)
                continue
            if not score:
                continue
            scores.append({"video_id": candidate.video_id, **score.model_dump()})

        selected_id = None
        if scores:
            scores.sort(key=lambda x: x.get("genel_puan", 0), reverse=True)
            for s in scores:
                if s["video_id"] not in used_ids:
                    selected_id = s["video_id"]
                    used_ids.add(selected_id)
                    break

        if selected_id:
            for c in candidates:
                if c.video_id == selected_id:
                    selected_videos.append(c)
                    break
        else:
            warnings.append(f"No unique best video for subtopic: {subtopic}")

        results.append(
            SubtopicResult(
                subtopic=subtopic,
                candidates_considered=len(candidates),
                top_scored=len(scores),
                selected_video_id=selected_id,
                scores=scores,
            )
        )

    _emit(progress_callback, ProgressEvent(stage="done", message="Playlist created", progress=1.0))

    playlist = PlaylistResult(
        run_id=run_id,
        topic=topic,
        subtopics=results,
        selected_videos=selected_videos,
        created_at=now_iso(),
        warnings=warnings,
    )

    save_playlist_result(run_dir, playlist)
    return playlist
