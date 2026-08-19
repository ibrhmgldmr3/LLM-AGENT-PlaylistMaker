"""Iki dilli kesif: ana dilin yaninda Ingilizce havuz genisletmesi."""

from src.config import AppConfig
from src.models import FilterOptions, MetadataScore, PlaylistRequest, Subtopic, VideoCandidate
from src.services import playlist_service
from src.services.metadata_ranker import score_candidate
from src.services.playlist_service import _build_queries


def _subtopic(title="ARIMA modeli", query="arima zaman serisi", query_en="arima time series"):
    return Subtopic(
        title=title,
        normalized_title="arima-modeli",
        search_query=query,
        search_query_en=query_en,
    )


# ------------------------------------------------------------------ sorgular

def test_english_query_is_added_when_enabled():
    queries = _build_queries("Konu", _subtopic(), FilterOptions(language="tr", include_english=True))
    assert queries == ["arima zaman serisi", "arima time series"]


def test_english_query_is_omitted_when_disabled():
    queries = _build_queries("Konu", _subtopic(), FilterOptions(language="tr", include_english=False))
    assert queries == ["arima zaman serisi"]


def test_no_duplicate_search_when_language_is_already_english():
    """Ingilizce secilmisken ikinci sorgu bosuna kota harcamamali."""
    queries = _build_queries("Topic", _subtopic(), FilterOptions(language="en", include_english=True))
    assert len(queries) == 1


def test_falls_back_to_title_when_llm_gave_no_english_query():
    subtopic = _subtopic(query_en=None)
    queries = _build_queries("Konu", subtopic, FilterOptions(language="tr", include_english=True))
    assert queries == ["arima zaman serisi", "ARIMA modeli"]


def test_identical_queries_are_not_run_twice():
    subtopic = _subtopic(query="arima time series", query_en="arima time series")
    queries = _build_queries("Konu", subtopic, FilterOptions(language="tr", include_english=True))
    assert queries == ["arima time series"]


# ------------------------------------------------------------------ skorlama

def _candidate(language):
    return VideoCandidate(video_id="v", url="u", title="ARIMA", language=language, duration_sec=900)


def test_english_video_is_not_penalised_when_bilingual_is_on():
    """Ingilizce icerigi BILEREK havuza alip sonra cezalandirmak celiskili olurdu."""
    filters_on = FilterOptions(language="tr", include_english=True)
    filters_off = FilterOptions(language="tr", include_english=False)

    on = score_candidate(_candidate("en"), "konu", filters_on, subtopic="ARIMA")
    off = score_candidate(_candidate("en"), "konu", filters_off, subtopic="ARIMA")

    assert on.language_match > 0 > off.language_match


def test_native_language_still_outranks_english():
    filters = FilterOptions(language="tr", include_english=True)
    turkish = score_candidate(_candidate("tr"), "konu", filters, subtopic="ARIMA")
    english = score_candidate(_candidate("en"), "konu", filters, subtopic="ARIMA")
    assert turkish.language_match > english.language_match


def test_unrelated_language_is_still_penalised():
    filters = FilterOptions(language="tr", include_english=True)
    german = score_candidate(_candidate("de"), "konu", filters, subtopic="ARIMA")
    assert german.language_match < 0


# ------------------------------------------------------------------ boru hatti

class BilingualLLM:
    def generate_subtopics(self, topic, language, max_items=6):
        return [{"title": "ARIMA modeli", "query": "arima zaman serisi", "query_en": "arima time series"}]


def _config(tmp_path, **overrides):
    values = dict(
        gemini_api_key="test",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
        retry_base_delay_sec=0.01,
    )
    values.update(overrides)
    config = AppConfig(**values)
    config.ensure_directories()
    return config


def _score(total):
    return MetadataScore(
        total=total, title_relevance=1.0, description_relevance=0.5, channel_quality=0.5,
        duration_fit=1.0, difficulty_fit=0.25, language_match=1.0, freshness=0.5,
        engagement=0.25, rationale=[],
    )


def test_both_queries_run_and_pools_are_merged(monkeypatch, tmp_path):
    config = _config(tmp_path)
    seen: list[str] = []

    def fake_search(config_, store, query, filters, logger=None, notes=None, **kw):
        seen.append(query)
        suffix = "tr" if "zaman" in query else "en"
        return [VideoCandidate(video_id=f"video-{suffix}", url="u", title=f"T {suffix}")]

    monkeypatch.setattr(playlist_service, "create_llm_provider", lambda config: BilingualLLM())
    monkeypatch.setattr(playlist_service, "search_candidates", fake_search)
    monkeypatch.setattr(
        playlist_service, "rank_candidates",
        lambda cands, t, s, f, **kw: [(c, _score(5.0 - i)) for i, c in enumerate(cands)],
    )
    monkeypatch.setattr(
        playlist_service, "get_transcript",
        lambda *a, **k: __import__("src.models", fromlist=["TranscriptResult"]).TranscriptResult(
            video_id="x", status="unavailable", source="none"),
    )

    result = playlist_service.build_playlist(
        config,
        PlaylistRequest(topic="Konu", filters=FilterOptions(language="tr", include_english=True)),
    )

    assert seen == ["arima zaman serisi", "arima time series"]
    # Iki aramadan gelen adaylar tek havuzda birlesmeli.
    assert result.subtopics[0].candidates_considered == 2


def test_only_one_query_runs_when_disabled(monkeypatch, tmp_path):
    config = _config(tmp_path)
    seen: list[str] = []

    def fake_search(config_, store, query, filters, logger=None, notes=None, **kw):
        seen.append(query)
        return [VideoCandidate(video_id="video-tr", url="u", title="T")]

    monkeypatch.setattr(playlist_service, "create_llm_provider", lambda config: BilingualLLM())
    monkeypatch.setattr(playlist_service, "search_candidates", fake_search)
    monkeypatch.setattr(
        playlist_service, "rank_candidates", lambda cands, t, s, f, **kw: [(cands[0], _score(5.0))]
    )
    monkeypatch.setattr(
        playlist_service, "get_transcript",
        lambda *a, **k: __import__("src.models", fromlist=["TranscriptResult"]).TranscriptResult(
            video_id="x", status="unavailable", source="none"),
    )

    playlist_service.build_playlist(
        config,
        PlaylistRequest(topic="Konu", filters=FilterOptions(language="tr", include_english=False)),
    )

    assert seen == ["arima zaman serisi"]
