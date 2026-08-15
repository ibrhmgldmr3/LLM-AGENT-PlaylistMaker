import pytest

from src.providers.errors import ProviderPermanentError, ProviderTemporaryError
from src.providers.llm_provider import _classify_gemini_error, _parse_subtopics


def test_depleted_credits_is_permanent_not_retried():
    """Regresyon: 429 ile gelen 'krediler tukendi' 3 kez bosuna tekrar deneniyordu."""
    exc = Exception(
        "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': "
        "'Your prepayment credits are depleted. Please go to AI Studio...'}}"
    )
    assert isinstance(_classify_gemini_error(exc), ProviderPermanentError)


def test_invalid_api_key_is_permanent():
    assert isinstance(_classify_gemini_error(Exception("400 API key not valid")), ProviderPermanentError)


def test_transient_server_error_is_temporary():
    assert isinstance(_classify_gemini_error(Exception("503 Service Unavailable")), ProviderTemporaryError)
    assert isinstance(_classify_gemini_error(Exception("timed out")), ProviderTemporaryError)


def _titles(items):
    return [item["title"] for item in items]


def test_parse_subtopics_accepts_plain_array():
    """Model duz metin listesi donerse sorgu alanlari bos kalir."""
    assert _parse_subtopics('["A", "B"]') == [
        {"title": "A", "query": "", "query_en": "", "terms": []},
        {"title": "B", "query": "", "query_en": "", "terms": []},
    ]


def test_parse_subtopics_unwraps_object_form():
    """Model diziyi bazen bir nesnenin icine sarmaliyor."""
    assert _titles(_parse_subtopics('{"subtopics": ["A", "B"]}')) == ["A", "B"]


def test_parse_subtopics_extracts_titles_from_dicts():
    assert _titles(_parse_subtopics('[{"title": "A"}, {"title": "B"}]')) == ["A", "B"]


def test_parse_subtopics_keeps_the_search_query():
    """Istenen bicim: her alt konu kendi arama sorgusunu tasir."""
    parsed = _parse_subtopics('[{"title": "ARIMA modeli", "query": "arima zaman serisi"}]')
    assert parsed == [
        {"title": "ARIMA modeli", "query": "arima zaman serisi", "query_en": "", "terms": []}
    ]


def test_parse_subtopics_keeps_the_english_query():
    """Iki dilli kesif icin ikinci sorgu da tasinmali."""
    parsed = _parse_subtopics(
        '[{"title": "ARIMA modeli", "query": "arima zaman serisi", "query_en": "arima time series"}]'
    )
    assert parsed[0]["query_en"] == "arima time series"


def test_parse_subtopics_accepts_alternative_key_names():
    parsed = _parse_subtopics('[{"subtopic": "A", "search_query": "a tutorial", "english_query": "a en"}]')
    assert parsed == [{"title": "A", "query": "a tutorial", "query_en": "a en", "terms": []}]


def test_parse_subtopics_rejects_non_json():
    with pytest.raises(ProviderTemporaryError):
        _parse_subtopics("not json at all")


def test_parse_subtopics_drops_blanks():
    assert _titles(_parse_subtopics('["A", "", "  ", "B"]')) == ["A", "B"]
