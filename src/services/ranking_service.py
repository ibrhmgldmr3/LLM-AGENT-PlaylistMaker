import json
from typing import Optional

from google import genai
from pydantic import ValidationError

from src.config import AppConfig
from src.models import VideoScore
from src.utils.retry_utils import retry_with_backoff


SYSTEM_PROMPT = "Return only valid JSON."


def _parse_score(text: str) -> VideoScore:
    data = json.loads(text)
    return VideoScore(**data)


def score_transcript(config: AppConfig, transcript_text: str, topic: str, logger=None) -> Optional[VideoScore]:
    client = genai.Client(api_key=config.gemini_api_key)

    def _call() -> VideoScore:
        prompt = (
            "Analyze the transcript for how well it matches the topic. "
            "Return JSON only with integer scores 0-10.\n"
            f"Topic: {topic}\n"
            "Transcript (truncated):\n"
            f"{transcript_text[:2000]}\n\n"
            "Return JSON with this schema:\n"
            "{"
            '"kapsam_uyumu": int,'
            '"bilgi_derinligi": int,'
            '"anlatim_tarzi": int,'
            '"hedef_kitle": int,'
            '"yapisal_tutarlilik": int,'
            '"genel_puan": int,'
            '"yorum": "short comment"'
            "}"
        )
        response = client.models.generate_content(
            model=config.gemini_model,
            contents=prompt,
            config={"system_instruction": SYSTEM_PROMPT, "response_mime_type": "application/json"},
        )
        text = (response.text or "").strip()
        if not text:
            raise ValueError("Empty LLM response")
        try:
            return _parse_score(text)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(f"Invalid score JSON: {exc}")

    return retry_with_backoff(
        _call,
        attempts=config.retry_max_attempts,
        base_delay=config.retry_base_delay_sec,
        logger=logger,
    )
