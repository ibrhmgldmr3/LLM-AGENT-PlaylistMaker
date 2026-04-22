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
