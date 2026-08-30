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
    OpenRouterLLMProvider,
    STUDY_NOTE_LEAK_RETRY_SUFFIX,
    SUBTOPIC_JSON_SCHEMA,
    SUBTOPIC_SCHEMA,
    TogetherLLMProvider,
    _classify_openrouter_error,
    _classify_together_error,
    _looks_like_reasoning_leak,
    _SplitLLMProvider,
    build_subtopic_prompt,
    create_llm_provider,
    create_rag_llm_provider,
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


# ------------------------------------------------------------- openrouter

def test_factory_picks_openrouter():
    provider = create_llm_provider(AppConfig(llm_provider="openrouter", openrouter_api_key="o"))
    assert isinstance(provider, OpenRouterLLMProvider)


def test_openrouter_without_a_key_fails_clearly():
    with pytest.raises(ProviderPermanentError, match="OPENROUTER_API_KEY"):
        create_llm_provider(AppConfig(llm_provider="openrouter"))


def test_openrouter_key_is_read_from_one_place():
    assert AppConfig(llm_provider="openrouter", openrouter_api_key="o").llm_api_key() == "o"
    assert AppConfig(llm_provider="openrouter", gemini_api_key="g").llm_api_key() is None


def test_openrouter_embed_fails_clearly():
    """OpenRouter'da embedding yok; sessizce baska modele dusmek yerine acik hata."""
    provider = OpenRouterLLMProvider(AppConfig(llm_provider="openrouter", openrouter_api_key="o"))
    with pytest.raises(ProviderPermanentError, match="embedding"):
        provider.embed(["metin"])


def test_embedding_falls_back_when_llm_is_openrouter():
    """OpenRouter embedding desteklemedigi icin embedding Gemini'de kalir."""
    config = AppConfig(llm_provider="openrouter", openrouter_api_key="o", gemini_api_key="g")
    assert config.effective_embedding_provider() == "gemini"
    assert config.embedding_model() == config.gemini_embedding_model


def test_embedding_provider_explicit_choice_wins():
    config = AppConfig(
        llm_provider="openrouter",
        openrouter_api_key="o",
        together_api_key="t",
        embedding_provider="together",
    )
    assert config.effective_embedding_provider() == "together"


def test_rag_provider_splits_generation_and_embedding():
    """RAG tek nesne tasiyor; uretim OpenRouter, embedding Gemini olmali."""
    config = AppConfig(llm_provider="openrouter", openrouter_api_key="o", gemini_api_key="g")
    provider = create_rag_llm_provider(config)

    assert isinstance(provider, _SplitLLMProvider)
    assert isinstance(provider._generation, OpenRouterLLMProvider)
    assert isinstance(provider._embedding, GeminiLLMProvider)


def test_rag_provider_does_not_wrap_when_same_provider():
    """Iki is ayni saglayicidaysa gereksiz sarmalama yapilmiyor."""
    provider = create_rag_llm_provider(AppConfig(gemini_api_key="g"))
    assert isinstance(provider, GeminiLLMProvider)


@pytest.mark.parametrize(
    "status_code,expected",
    [
        (429, ProviderRateLimitedError),
        (503, ProviderTemporaryError),
        (401, ProviderPermanentError),
        (400, ProviderPermanentError),
    ],
)
def test_openrouter_errors_are_classified(status_code, expected):
    assert isinstance(_classify_openrouter_error(status_code, "govde"), expected)


# --------------------------------------------------- reasoning leak guard
#
# Calisma notu serbest metin oldugu icin SUBTOPIC_SCHEMA gibi protokol
# seviyesinde bir koruma yok. Gozlemlendi: OpenRouter'in `openrouter/free`
# otomatik yonlendiricisi bazen zayif bir modele dusup "no preamble" istemini
# gormezden geliyor ve ham akil yurutmesini ("Here's a thinking process...")
# calisma notu diye donduruyordu. `_looks_like_reasoning_leak` bu en bariz
# sizintiyi yakalar; her uc saglayici da yakalayinca bir kez daha, daha sert
# bir talimatla dener ve hala sizintiyi gormeye devam ederse notu SESSIZCE
# kullaniciya gostermek yerine hata yukseltir.

def test_reasoning_leak_marker_is_detected():
    assert _looks_like_reasoning_leak("Here's a thinking process: first I will...")
    assert _looks_like_reasoning_leak("<think>uzun akil yurutme</think>Not metni")


def test_reasoning_leak_length_heuristic_catches_marker_free_dumps():
    assert _looks_like_reasoning_leak("x" * 6001)


def test_normal_study_note_is_not_flagged():
    note = "Bu video useState'i anlatiyor.\n\n- Madde 1\n- Madde 2\n\n**Terms**\nuseState"
    assert not _looks_like_reasoning_leak(note)


def test_gemini_study_note_retries_once_then_returns_clean_text(monkeypatch):
    provider = GeminiLLMProvider.__new__(GeminiLLMProvider)
    provider.config = AppConfig(gemini_api_key="g")
    provider._thinking_unsupported = set()

    calls = []

    def fake_generate(model_name, prompt, config_factory):
        calls.append(prompt)
        if len(calls) == 1:
            return "Here's a thinking process: blah " * 50
        return "Temiz not."

    monkeypatch.setattr(provider, "_generate", fake_generate)

    result = provider.generate_study_note("Konu", "Alt", "Video", "transkript", "tr")

    assert result == "Temiz not."
    assert len(calls) == 2
    assert calls[1].endswith(STUDY_NOTE_LEAK_RETRY_SUFFIX)


def test_gemini_study_note_gives_up_after_persistent_leak(monkeypatch):
    """Tum aday modeller de sizinti dondurunce not UYDURULMAZ, acik hata verilir."""
    provider = GeminiLLMProvider.__new__(GeminiLLMProvider)
    provider.config = AppConfig(gemini_api_key="g")
    provider._thinking_unsupported = set()

    monkeypatch.setattr(provider, "_generate", lambda *a, **k: "Here's a thinking process: " * 100)

    with pytest.raises(ProviderPermanentError, match="reasoning leak"):
        provider.generate_study_note("Konu", "Alt", "Video", "transkript", "tr")


def test_together_study_note_raises_temporary_error_when_leak_persists(monkeypatch):
    provider = TogetherLLMProvider(AppConfig(llm_provider="together", together_api_key="t"))
    monkeypatch.setattr(provider, "_generate", lambda *a, **k: "Here's a thinking process: " * 100)

    with pytest.raises(ProviderTemporaryError, match="reasoning leak"):
        provider.generate_study_note("Konu", "Alt", "Video", "transkript", "tr")


def test_openrouter_study_note_retries_once_then_returns_clean_text(monkeypatch):
    provider = OpenRouterLLMProvider(AppConfig(llm_provider="openrouter", openrouter_api_key="o"))
    calls = []

    def fake_generate(prompt, system_instruction):
        calls.append(prompt)
        if len(calls) == 1:
            return "Here's a thinking process: blah " * 50
        return "Temiz not."

    monkeypatch.setattr(provider, "_generate", fake_generate)

    result = provider.generate_study_note("Konu", "Alt", "Video", "transkript", "tr")

    assert result == "Temiz not."
    assert len(calls) == 2
    assert calls[1].endswith(STUDY_NOTE_LEAK_RETRY_SUFFIX)
