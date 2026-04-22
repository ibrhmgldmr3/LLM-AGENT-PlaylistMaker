from __future__ import annotations

from difflib import SequenceMatcher

from src.models import Subtopic
from src.providers.llm_provider import LLMProvider
from src.utils.retry_utils import retry_with_backoff
from src.utils.text_utils import normalize_text, slugify_text


def generate_subtopics(provider: LLMProvider, topic: str, language: str, logger=None) -> list[Subtopic]:
    attempts = getattr(getattr(provider, "config", None), "retry_max_attempts", 3)
    base_delay = getattr(getattr(provider, "config", None), "retry_base_delay_sec", 1.0)
    raw_items = retry_with_backoff(
        lambda: provider.generate_subtopics(topic, language),
        attempts=attempts,
        base_delay=base_delay,
        logger=logger,
    )
    results: list[Subtopic] = []
    for item in raw_items:
        title = normalize_text(item)
        if not title:
            continue
        normalized = slugify_text(title)
        if not normalized:
            continue
        merged = False
        for existing in results:
            if existing.normalized_title == normalized or _similar(existing.title, title) >= 0.87:
                if title not in existing.source_titles:
                    existing.source_titles.append(title)
                merged = True
                break
        if merged:
            continue
        results.append(Subtopic(title=title, normalized_title=normalized, source_titles=[title]))
    if logger:
        logger.info("Generated %s normalized subtopics", len(results))
    return results


def _similar(left: str, right: str) -> float:
    return SequenceMatcher(a=left.lower(), b=right.lower()).ratio()
