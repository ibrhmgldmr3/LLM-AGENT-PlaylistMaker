from __future__ import annotations

from datetime import datetime, timezone

from src.models import FilterOptions, MetadataScore, VideoCandidate
from src.utils.text_utils import keyword_overlap_score, normalize_text


def rank_candidates(
    candidates: list[VideoCandidate],
    topic: str,
    subtopic: str,
    filters: FilterOptions,
) -> list[tuple[VideoCandidate, MetadataScore]]:
    ranked: list[tuple[VideoCandidate, MetadataScore]] = []
    query_terms = normalize_text(f"{topic} {subtopic}")
    for candidate in candidates:
        score = score_candidate(candidate, query_terms, filters)
        candidate.metadata_score = score.total
        ranked.append((candidate, score))
    ranked.sort(key=lambda item: item[1].total, reverse=True)
    return ranked


def score_candidate(candidate: VideoCandidate, query_terms: str, filters: FilterOptions) -> MetadataScore:
    title_relevance = 4.0 * keyword_overlap_score(candidate.title, query_terms)
    description_relevance = 2.0 * keyword_overlap_score(candidate.description, query_terms)
    channel_quality = _channel_quality_score(candidate)
    duration_fit = _duration_fit_score(candidate.duration_sec, filters.max_duration_minutes)
    difficulty_fit = _difficulty_fit_score(candidate, filters.difficulty)
    language_match = _language_match_score(candidate.language, filters.language)
    freshness = _freshness_score(candidate.publish_date, filters.freshness_preference)
    engagement = _engagement_score(candidate.view_count)
    penalty = -2.0 if candidate.is_live else 0.0
    total = (
        title_relevance
        + description_relevance
        + channel_quality
        + duration_fit
        + difficulty_fit
        + language_match
        + freshness
        + engagement
        + penalty
    )
    rationale = []
    if title_relevance >= 2.0:
        rationale.append("title strongly matches the learning query")
    if description_relevance >= 1.0:
        rationale.append("description reinforces the topic fit")
    if channel_quality >= 1.5:
        rationale.append("channel signals look reliable for learning content")
    if duration_fit >= 1.5:
        rationale.append("duration fits the study session target")
    if difficulty_fit >= 0.75:
        rationale.append("difficulty fit matches the requested level")
    if language_match >= 1.0:
        rationale.append("language matches the requested preference")
    if freshness >= 1.0:
        rationale.append("publish date aligns with the freshness preference")
    return MetadataScore(
        total=round(total, 3),
        title_relevance=round(title_relevance, 3),
        description_relevance=round(description_relevance, 3),
        channel_quality=round(channel_quality, 3),
        duration_fit=round(duration_fit, 3),
        difficulty_fit=round(difficulty_fit, 3),
        language_match=round(language_match, 3),
        freshness=round(freshness, 3),
        engagement=round(engagement, 3),
        rationale=rationale,
    )


def _channel_quality_score(candidate: VideoCandidate) -> float:
    channel = (candidate.channel or "").lower()
    score = candidate.channel_quality_score
    trusted_terms = ["official", "academy", "university", "course", "tutorial", "institute", "docs"]
    for term in trusted_terms:
        if term in channel:
            score += 0.35
    if candidate.view_count and candidate.view_count >= 50_000:
        score += 0.4
    return min(score, 2.0)


def _duration_fit_score(duration_sec: int | None, max_duration_minutes: int) -> float:
    if not duration_sec:
        return 0.25
    upper_bound = max_duration_minutes * 60
    if 480 <= duration_sec <= upper_bound:
        return 2.0
    if 180 <= duration_sec <= int(upper_bound * 1.2):
        return 1.25
    if duration_sec < 180:
        return 0.5
    return -0.25


def _language_match_score(candidate_language: str | None, requested_language: str) -> float:
    if not candidate_language:
        return 0.25
    if candidate_language.lower().startswith(requested_language.lower()):
        return 1.5
    return -0.2


def _difficulty_fit_score(candidate: VideoCandidate, difficulty: str) -> float:
    if difficulty == "mixed":
        return 0.25
    text = normalize_text(f"{candidate.title} {candidate.description}").lower()
    if difficulty == "beginner":
        beginner_terms = ["beginner", "intro", "introduction", "basics", "fundamentals", "start"]
        return 1.0 if any(term in text for term in beginner_terms) else 0.1
    if difficulty == "advanced":
        advanced_terms = ["advanced", "deep dive", "architecture", "internals", "expert"]
        return 1.0 if any(term in text for term in advanced_terms) else -0.1
    intermediate_terms = ["intermediate", "project", "applied", "practical", "walkthrough"]
    return 0.75 if any(term in text for term in intermediate_terms) else 0.1


def _freshness_score(publish_date: str | None, preference: str) -> float:
    if not publish_date:
        return 0.0
    published = _parse_date(publish_date)
    if not published:
        return 0.0
    age_days = max((datetime.now(timezone.utc) - published).days, 0)
    if preference == "recent":
        if age_days <= 365:
            return 1.5
        if age_days <= 730:
            return 0.75
        return -0.25
    if preference == "evergreen":
        if 180 <= age_days <= 1825:
            return 1.0
        return 0.5
    if age_days <= 1095:
        return 1.0
    return 0.25


def _engagement_score(view_count: int | None) -> float:
    if not view_count:
        return 0.0
    if view_count >= 500_000:
        return 1.0
    if view_count >= 50_000:
        return 0.5
    if view_count >= 5_000:
        return 0.2
    return 0.0


def _parse_date(value: str) -> datetime | None:
    formats = ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%Y%m%d")
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
