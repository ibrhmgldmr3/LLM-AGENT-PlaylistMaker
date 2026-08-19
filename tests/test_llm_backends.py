"""LLM saglayicisinin degistirilebilirligi.

Together.ai OpenAI uyumlu bir uc sunuyor; bu testler ag cagrisi YAPMADAN
sozlesmeyi ve secim mantigini kilitliyor.
"""

from __future__ import annotations

import pytest

from src.config import AppConfig
from src.providers.errors import (
    ProviderPermanentError,
    ProviderRateLimitedError,
    ProviderTemporaryError,
)
from src.providers.llm_provider import (
    GeminiLLMProvider,
    SUBTOPIC_JSON_SCHEMA,
    SUBTOPIC_SCHEMA,
    TogetherLLMProvider,
    _classify_together_error,
    build_subtopic_prompt,
    create_llm_provider,
)


# ----------------------------------------------------------------- fabrika

def test_factory_picks_the_configured_provider():
    together = create_llm_provider(AppConfig(llm_provider="together", together_api_key="t"))
    assert isinstance(together, TogetherLLMProvider)


def test_factory_defaults_to_gemini():
    """Varsayilan degismedi: mevcut kurulumlar oldugu gibi calisiyor."""
    assert AppConfig(gemini_api_key="g").llm_provider == "gemini"


def test_together_without_a_key_fails_clearly():
    with pytest.raises(ProviderPermanentError, match="TOGETHER_API_KEY"):
        create_llm_provider(AppConfig(llm_provider="together"))


def test_selected_provider_key_is_read_from_one_place():
    """`llm_api_key()` saglayici secimini tek yerde topluyor."""
    assert AppConfig(gemini_api_key="g").llm_api_key() == "g"
    assert AppConfig(llm_provider="together", together_api_key="t").llm_api_key() == "t"
    assert AppConfig(llm_provider="together", gemini_api_key="g").llm_api_key() is None


# ------------------------------------------------------------------ istem

def test_both_providers_share_one_prompt():
    """Istem saglayici sinifinin ICINDE olsaydi ikisi sessizce ayrisirdi."""
    import inspect

    assert "build_subtopic_prompt" in inspect.getsource(GeminiLLMProvider.generate_subtopics)
    assert "build_subtopic_prompt" in inspect.getsource(TogetherLLMProvider.generate_subtopics)


def test_prompt_asks_for_all_four_keys():
    """Regresyon: istem "exactly three keys" derken sema DORT alan istiyordu.

    Gemini'de `response_schema` semayi zorladigi icin celiski fark edilmemisti
    -- alan yine de geliyordu. OpenAI uyumlu uclarda sema zorlamasi modele gore
    degistigi icin ayni celiski Together'da alanin sessizce kaybolmasi demek
    olurdu.
    """
    prompt = build_subtopic_prompt("Kalman filtresi", "tr", 6)

    assert "four keys" in prompt
    for key in ('"title"', '"query"', '"query_en"', '"terms"'):
        assert key in prompt, f"{key} istemde gecmiyor"


def test_both_schemas_demand_the_same_fields():
    """Iki lehce, TEK sozlesme: alanlar ve zorunluluklar ayni olmali."""
    gemini = SUBTOPIC_SCHEMA["items"]
    openai = SUBTOPIC_JSON_SCHEMA["items"]

    assert set(gemini["properties"]) == set(openai["properties"])
    assert gemini["required"] == openai["required"]


# ------------------------------------------------------ hata siniflandirma

@pytest.mark.parametrize(
    "status_code,expected",
    [
        (429, ProviderRateLimitedError),
        (503, ProviderTemporaryError),
        (500, ProviderTemporaryError),
        (401, ProviderPermanentError),
        (400, ProviderPermanentError),
    ],
)
def test_together_errors_are_classified(status_code, expected):
    """4xx'in tamami kalici DEGIL.

    429 hiz siniri ve 5xx tekrar denenebilir; ayrimi yapmamak gecici bir
    sikisikligi kalici hata sayip calistirmayi bosuna dusururdu.
    """
    assert isinstance(_classify_together_error(status_code, "govde"), expected)


def test_together_error_does_not_leak_the_whole_body():
    """Cok uzun govdeler hata mesajina tasinmamali."""
    error = _classify_together_error(400, "x" * 5000)

    assert len(str(error)) < 500
