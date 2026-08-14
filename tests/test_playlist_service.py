from pathlib import Path

from src.config import AppConfig
from src.models import FilterOptions, MetadataScore, PlaylistRequest, TranscriptResult, VideoCandidate
from src.services import playlist_service


class DummyLLM:
    def __init__(self, items=None):
        # DIKKAT: `items or [...]` bos listeyi de varsayilana dusururdu.
        self.items = ["Foundations", "Advanced Practice"] if items is None else items

    def generate_subtopics(self, topic: str, language: str, max_items: int = 6):
        return self.items[:max_items]


def _config(tmp_path, **overrides):
    values = dict(
        gemini_api_key="test",
        gemini_model="gemini-test",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
        retry_base_delay_sec=0.01,
    )
    values.update(overrides)
    config = AppConfig(**values)
    config.ensure_directories()
    return config


def _score(total, rationale="strong metadata match"):
    return MetadataScore(
        total=total,
        title_relevance=3.0,
        description_relevance=1.5,
        channel_quality=1.5,
        duration_fit=1.0,
        difficulty_fit=0.5,
        language_match=1.0,
        freshness=0.5,
        engagement=0.5,
        rationale=[rationale],
    )


def _candidates():
    return [
        VideoCandidate(
            video_id="video-1",
            url="https://www.youtube.com/watch?v=video-1",
            title="Topic Foundations",
            description="Foundations and setup",
            channel="Official Academy",
            duration_sec=1500,
            view_count=500_000,
            language="en",
        ),
        VideoCandidate(
            video_id="video-2",
            url="https://www.youtube.com/watch?v=video-2",
            title="Topic Advanced Practice",
            description="Advanced drills and exercises",
            channel="Official Academy",
            duration_sec=1800,
            view_count=350_000,
            language="en",
        ),
    ]


def test_playlist_prevents_duplicate_processing_and_exports(monkeypatch, tmp_path):
    config = _config(tmp_path)
    candidates = _candidates()
    transcript_calls = {"video-1": 0, "video-2": 0}

    monkeypatch.setattr(playlist_service, "GeminiLLMProvider", lambda config: DummyLLM())
    monkeypatch.setattr(playlist_service, "search_candidates", lambda *a, **k: candidates)
    monkeypatch.setattr(
        playlist_service,
        "rank_candidates",
        lambda cands, topic, subtopic, filters: [(candidates[0], _score(8.0)), (candidates[1], _score(7.0))],
    )

    def fake_get_transcript(config, store, candidate, run_dir, state, logger=None, preferred_language=None):
        transcript_calls[candidate.video_id] += 1
        return TranscriptResult(
            video_id=candidate.video_id,
            status="available",
            source="youtube_transcript_api",
            text=f"{candidate.title} transcript",
        )

    monkeypatch.setattr(playlist_service, "get_transcript", fake_get_transcript)

    request = PlaylistRequest(topic="Test Topic", filters=FilterOptions(language="en"))
    result = playlist_service.build_playlist(config, request)

    assert [item.video.video_id for item in result.recommendations] == ["video-1", "video-2"]
    assert transcript_calls["video-1"] == 1
    assert transcript_calls["video-2"] == 1
    assert result.exports is not None
    assert Path(result.exports.json_path).exists()
    assert Path(result.exports.markdown_path).exists()


def test_caller_can_supply_run_id_and_user(monkeypatch, tmp_path):
    """Faz 0: API isi arka plana atmadan ONCE kimligi bilmeli.

    `run_id` disaridan verilebilmeli ki cagirana hemen is numarasi donebilelim.
    """
    from src.storage import SQLiteStore

    config = _config(tmp_path)
    candidates = _candidates()

    monkeypatch.setattr(playlist_service, "GeminiLLMProvider", lambda config: DummyLLM(["Foundations"]))
    monkeypatch.setattr(playlist_service, "search_candidates", lambda *a, **k: candidates)
    monkeypatch.setattr(
        playlist_service,
        "rank_candidates",
        lambda cands, topic, subtopic, filters: [(candidates[0], _score(8.0))],
    )
    monkeypatch.setattr(
        playlist_service,
        "get_transcript",
        lambda *a, **k: TranscriptResult(video_id="video-1", status="unavailable", source="none"),
    )

    result = playlist_service.build_playlist(
        config,
        PlaylistRequest(topic="Test", filters=FilterOptions(language="en")),
        run_id="onceden-belirlenen-id",
        user_id="ali",
    )

    assert result.run_id == "onceden-belirlenen-id"
    store = SQLiteStore(config.sqlite_path)
    assert store.get_run_summary("onceden-belirlenen-id")["user_id"] == "ali"
    # Sonuc gecmisten geri okunabilmeli.
    assert store.get_run("onceden-belirlenen-id").topic == "Test"


def test_run_id_is_generated_when_not_supplied(monkeypatch, tmp_path):
    config = _config(tmp_path)
    monkeypatch.setattr(playlist_service, "GeminiLLMProvider", lambda config: DummyLLM([]))

    result = playlist_service.build_playlist(config, PlaylistRequest(topic="Test"))

    assert len(result.run_id) == 32  # uuid4().hex


def test_publish_failure_warning_reaches_the_result(monkeypatch, tmp_path):
    """Regresyon: pydantic listeyi kopyaladigi icin uyari `result.warnings`'e ulasmiyordu."""
    config = _config(tmp_path)
    candidates = _candidates()

    monkeypatch.setattr(playlist_service, "GeminiLLMProvider", lambda config: DummyLLM(["Foundations"]))
    monkeypatch.setattr(playlist_service, "search_candidates", lambda *a, **k: candidates)
    monkeypatch.setattr(
        playlist_service,
        "rank_candidates",
        lambda cands, topic, subtopic, filters: [(candidates[0], _score(8.0))],
    )
    monkeypatch.setattr(
        playlist_service,
        "get_transcript",
        lambda *a, **k: TranscriptResult(video_id="video-1", status="unavailable", source="none"),
    )

    def boom(*args, **kwargs):
        raise RuntimeError("OAuth reddedildi")

    monkeypatch.setattr(playlist_service, "create_youtube_playlist", boom)

    request = PlaylistRequest(
        topic="Test Topic", filters=FilterOptions(language="en"), create_youtube_playlist=True
    )
    result = playlist_service.build_playlist(config, request)

    assert any("OAuth reddedildi" in warning for warning in result.warnings)
    assert result.published_playlist_url is None
    # Uyari dis aktarilan JSON'a da yansimali.
    assert "OAuth reddedildi" in Path(result.exports.json_path).read_text(encoding="utf-8")


def test_empty_subtopics_produce_a_warning(monkeypatch, tmp_path):
    """Regresyon: bos alt konu listesi sessizce bos bir playlist uretiyordu."""
    config = _config(tmp_path)
    monkeypatch.setattr(playlist_service, "GeminiLLMProvider", lambda config: DummyLLM([]))

    result = playlist_service.build_playlist(config, PlaylistRequest(topic="Test"))

    assert result.recommendations == []
    assert result.warnings
    assert result.exports is not None


def test_selection_falls_back_beyond_the_shortlist(monkeypatch, tmp_path):
    """Regresyon: top-k tukendiginde geriye kalan adaylar hic degerlendirilmiyordu."""
    config = _config(tmp_path, metadata_top_k=1, transcript_enrichment_top_k=1)
    pool = [
        VideoCandidate(video_id=f"video-{i}", url=f"https://youtu.be/video-{i}", title=f"Video {i}")
        for i in range(3)
    ]

    monkeypatch.setattr(
        playlist_service, "GeminiLLMProvider", lambda config: DummyLLM(["Alpha", "Beta", "Gamma"])
    )
    monkeypatch.setattr(playlist_service, "search_candidates", lambda *a, **k: pool)
    monkeypatch.setattr(
        playlist_service,
        "rank_candidates",
        lambda cands, topic, subtopic, filters: [(pool[i], _score(9.0 - i)) for i in range(3)],
    )
    monkeypatch.setattr(
        playlist_service,
        "get_transcript",
        lambda config, store, candidate, run_dir, state, logger=None, preferred_language=None: TranscriptResult(
            video_id=candidate.video_id, status="unavailable", source="none"
        ),
    )

    result = playlist_service.build_playlist(config, PlaylistRequest(topic="Test"))

    assert [item.video.video_id for item in result.recommendations] == ["video-0", "video-1", "video-2"]


def test_transcript_enrichment_is_capped(monkeypatch, tmp_path):
    config = _config(tmp_path, metadata_top_k=4, transcript_enrichment_top_k=2)
    pool = [
        VideoCandidate(video_id=f"video-{i}", url=f"https://youtu.be/video-{i}", title=f"Video {i}")
        for i in range(4)
    ]
    enriched: list[str] = []

    monkeypatch.setattr(playlist_service, "GeminiLLMProvider", lambda config: DummyLLM(["Alpha"]))
    monkeypatch.setattr(playlist_service, "search_candidates", lambda *a, **k: pool)
    monkeypatch.setattr(
        playlist_service,
        "rank_candidates",
        lambda cands, topic, subtopic, filters: [(pool[i], _score(9.0 - i)) for i in range(4)],
    )

    def fake_get_transcript(config, store, candidate, run_dir, state, logger=None, preferred_language=None):
        enriched.append(candidate.video_id)
        return TranscriptResult(video_id=candidate.video_id, status="unavailable", source="none")

    monkeypatch.setattr(playlist_service, "get_transcript", fake_get_transcript)

    playlist_service.build_playlist(config, PlaylistRequest(topic="Test"))

    assert enriched == ["video-0", "video-1"]


def test_subtopic_count_respects_config_cap(monkeypatch, tmp_path):
    config = _config(tmp_path, max_subtopics=2)
    searches: list[str] = []
    pool = [VideoCandidate(video_id="v", url="https://youtu.be/v", title="V")]

    monkeypatch.setattr(
        playlist_service, "GeminiLLMProvider", lambda config: DummyLLM([f"S{i}" for i in range(10)])
    )

    def fake_search(config, store, query, filters, logger=None, notes=None):
        searches.append(query)
        return pool

    monkeypatch.setattr(playlist_service, "search_candidates", fake_search)
    monkeypatch.setattr(
        playlist_service, "rank_candidates", lambda cands, t, s, f: [(pool[0], _score(5.0))]
    )
    monkeypatch.setattr(
        playlist_service,
        "get_transcript",
        lambda *a, **k: TranscriptResult(video_id="v", status="unavailable", source="none"),
    )

    playlist_service.build_playlist(config, PlaylistRequest(topic="Test"))

    assert len(searches) == 2
