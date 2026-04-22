from typing import Iterable


def title_relevance_score(title: str, keywords: Iterable[str]) -> float:
    if not title:
        return 0.0
    lower_title = title.lower()
    score = 0.0
    for kw in keywords:
        kw = kw.lower().strip()
        if not kw:
            continue
        if kw in lower_title:
            score += 1.0
    return score


def duration_score(duration_sec: int | None) -> float:
    if not duration_sec:
        return 0.0
    # Prefer 5-60 minutes
    if 300 <= duration_sec <= 3600:
        return 2.0
    if 180 <= duration_sec <= 5400:
        return 1.0
    return 0.0
