from __future__ import annotations

import json
import warnings
from typing import Protocol

from src.config import AppConfig


class LLMProvider(Protocol):
    def generate_subtopics(self, topic: str, language: str) -> list[str]:
        raise NotImplementedError


class GeminiLLMProvider:
    def __init__(self, config: AppConfig):
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r".*<built-in function any> is not a Python type.*",
                category=UserWarning,
            )
            from google import genai

        self.config = config
        self.client = genai.Client(api_key=config.gemini_api_key)
        self.fallback_model = "gemini-2.5-flash"

    def generate_subtopics(self, topic: str, language: str) -> list[str]:
        prompt = (
            "You are planning a learning playlist. "
            "Return a JSON array of concise subtopics that cover the topic end-to-end. "
            "Avoid duplicates, near-duplicates, and overly generic labels. "
            f"Preferred output language: {language}. "
            f"Topic: {topic}"
        )

        errors: list[str] = []
        for model_name in self._candidate_models():
            try:
                response = self.client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config={
                        "response_mime_type": "application/json",
                        "system_instruction": "Return only valid JSON.",
                    },
                )
                text = (response.text or "").strip()
                if not text:
                    raise ValueError("Empty Gemini response")
                payload = json.loads(text)
                if not isinstance(payload, list):
                    raise ValueError("Gemini response was not a JSON array")
                return [str(item).strip() for item in payload if str(item).strip()]
            except Exception as exc:
                errors.append(f"{model_name}: {exc}")
                if not _is_model_not_found_error(exc):
                    raise
        raise RuntimeError("No usable Gemini model found. " + " | ".join(errors))

    def _candidate_models(self) -> list[str]:
        configured = self.config.gemini_model.strip()
        models = [configured]
        if configured != self.fallback_model:
            models.append(self.fallback_model)
        return models


def _is_model_not_found_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "not_found" in message or "is not found for api version" in message or "404" in message
