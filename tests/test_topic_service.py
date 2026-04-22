import types

from src.services import topic_service
from src.config import AppConfig


class DummyResponse:
    def __init__(self, text):
        self.text = text


class DummyClient:
    def __init__(self, *args, **kwargs):
        self.models = types.SimpleNamespace(generate_content=self.generate_content)

    @staticmethod
    def generate_content(*args, **kwargs):
        return DummyResponse('["Topic A", "Topic B", "Topic A"]')


def test_generate_subtopics_dedup(monkeypatch):
    monkeypatch.setattr(topic_service.genai, "Client", DummyClient)
    config = AppConfig(
        GEMINI_API_KEY="x",
        GEMINI_MODEL="test",
        YOUTUBE_SEARCH_PER_TOPIC=8,
        TRANSCRIPT_SCORE_TOP_K=3,
        REQUEST_TIMEOUT_SEC=10,
        RETRY_MAX_ATTEMPTS=1,
        RETRY_BASE_DELAY_SEC=0.1,
    )
    items = topic_service.generate_subtopics(config, "test")
    assert items == ["Topic A", "Topic B"]
