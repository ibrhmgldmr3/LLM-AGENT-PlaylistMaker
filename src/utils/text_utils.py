import re
from typing import Optional


VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})")


def extract_video_id(url_or_id: str) -> Optional[str]:
    if not url_or_id:
        return None
    if len(url_or_id) == 11 and re.match(r"^[a-zA-Z0-9_-]{11}$", url_or_id):
        return url_or_id
    match = VIDEO_ID_RE.search(url_or_id)
    if match:
        return match.group(1)
    return None


def normalize_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def slugify_text(text: str) -> str:
    normalized = normalize_text(text).lower()
    normalized = re.sub(r"[^a-z0-9\s-]", "", normalized)
    normalized = re.sub(r"[\s-]+", "-", normalized)
    return normalized.strip("-")


def keyword_overlap_score(text: str, query: str) -> float:
    query_tokens = {token for token in slugify_text(query).split("-") if token}
    text_tokens = {token for token in slugify_text(text).split("-") if token}
    if not query_tokens or not text_tokens:
        return 0.0
    return len(query_tokens & text_tokens) / len(query_tokens)
