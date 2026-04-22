from __future__ import annotations

from src.models import MetadataScore, Recommendation, TranscriptResult, VideoCandidate
from src.utils.text_utils import keyword_overlap_score, normalize_text


def select_recommendation(
    subtopic: str,
    ranked_candidates: list[tuple[VideoCandidate, MetadataScore]],
    transcripts: dict[str, TranscriptResult],
    used_video_ids: set[str],
    position: int,
) -> Recommendation | None:
    for candidate, metadata_score in ranked_candidates:
        if candidate.video_id in used_video_ids:
            continue
        transcript = transcripts.get(candidate.video_id)
        transcript_bonus, transcript_reason = _transcript_bonus(transcript, subtopic)
        confidence = round(min(10.0, metadata_score.total + transcript_bonus), 2)
        why_parts = metadata_score.rationale[:]
        if transcript_reason:
            why_parts.append(transcript_reason)
        if not why_parts:
            why_parts.append("selected as the strongest available metadata match")
        used_video_ids.add(candidate.video_id)
        return Recommendation(
            position=position,
            subtopic=subtopic,
            video=candidate,
            why_selected="; ".join(why_parts),
            confidence_score=confidence,
            transcript_status=transcript.status if transcript else "unavailable",
            transcript_source=transcript.source if transcript else None,
            transcript_backend=transcript.backend if transcript else None,
            metadata_score=metadata_score,
        )
    return None


def _transcript_bonus(transcript: TranscriptResult | None, subtopic: str) -> tuple[float, str | None]:
    if transcript is None:
        return 0.0, None
    if transcript.status != "available" or not transcript.text:
        return -0.1, "transcript enrichment was unavailable, so the pick relies on metadata"
    overlap = keyword_overlap_score(transcript.text[:4000], normalize_text(subtopic))
    if overlap >= 0.25:
        return 1.0, "transcript coverage reinforces the subtopic fit"
    if overlap > 0:
        return 0.35, "transcript partially reinforces the subtopic fit"
    return 0.0, "transcript was available but added limited extra evidence"
