import json
from typing import List

from google import genai

from src.config import AppConfig
from src.utils.retry_utils import retry_with_backoff
from src.utils.text_utils import normalize_text


SYSTEM_PROMPT = "Return only valid JSON."


def _parse_subtopics(text: str) -> List[str]:
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [normalize_text(str(x)) for x in data if normalize_text(str(x))]
    except json.JSONDecodeError:
        pass

    lines = [normalize_text(line.strip("-0123456789. ")) for line in text.splitlines()]
    return [line for line in lines if line]


def generate_subtopics(config: AppConfig, topic: str, logger=None) -> List[str]:
    client = genai.Client(api_key=config.gemini_api_key)

    def _call() -> List[str]:
        response = client.models.generate_content(
            model=config.gemini_model,
            contents=(
                "List the key subtopics someone should learn for this topic. "
                "Return a JSON array of strings only, no extra text. "
                f"Topic: {topic}"
            ),
            config={"system_instruction": SYSTEM_PROMPT, "response_mime_type": "application/json"},
        )
        text = response.text or ""
        items = _parse_subtopics(text)
        if not items:
            raise ValueError("No subtopics parsed")
        seen = set()
        deduped = []
        for item in items:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    return retry_with_backoff(
        _call,
        attempts=config.retry_max_attempts,
        base_delay=config.retry_base_delay_sec,
        logger=logger,
    )
