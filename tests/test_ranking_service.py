import types

from src.services import ranking_service
from src.config import AppConfig


class DummyResponse:
    def __init__(self, text):
        self.text = text


class DummyClient:
    def __init__(self, *args, **kwargs):
        self.models = types.SimpleNamespace(generate_content=self.generate_content)

    @staticmethod
    def generate_content(*args, **kwargs):
        return DummyResponse('{"kapsam_uyumu":8,"bilgi_derinligi":7,"anlatim_tarzi":7,"hedef_kitle":6,"yapisal_tutarlilik":7,"genel_puan":7,"yorum":"ok"}')


def test_score_transcript_valid(monkeypatch):
    monkeypatch.setattr(ranking_service.genai, "Client", DummyClient)
    config = AppConfig(
        GEMINI_API_KEY="x",
        GEMINI_MODEL="test",
        YOUTUBE_SEARCH_PER_TOPIC=8,
        TRANSCRIPT_SCORE_TOP_K=3,
        REQUEST_TIMEOUT_SEC=10,
        RETRY_MAX_ATTEMPTS=1,
        RETRY_BASE_DELAY_SEC=0.1,
    )
    score = ranking_service.score_transcript(config, "text", "topic")
    assert score.genel_puan == 7
