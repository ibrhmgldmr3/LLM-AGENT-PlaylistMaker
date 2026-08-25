"""Calisma notu ozelligi: transkriptten DOGRUDAN baglamla uretim, RAG DEGIL.

Iki katman test ediliyor:
1. `_generate_study_notes` -- transkript durumuna gore dogru StudyNote uretimi
   (uydurma yasagi, hata yutulmasi, es zamanlilik).
2. `build_playlist` uctan uca -- ozellik VARSAYILAN KAPALI iken hicbir sey
   degismiyor, ACIKKEN sonuca `study_notes` ekleniyor.
"""

from __future__ import annotations

from src.config import AppConfig
from src.models import (
    FilterOptions,
    MetadataScore,
    PlaylistRequest,
    Subtopic,
    TranscriptResult,
    VideoCandidate,
)
from src.providers.errors import ProviderTemporaryError
from src.services import playlist_service
from src.services.playlist_service import _SubtopicWork, _generate_study_notes


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


def _score(total=8.0):
    return MetadataScore(
        total=total, title_relevance=3.0, description_relevance=1.0, channel_quality=1.0,
        duration_fit=1.0, difficulty_fit=0.5, language_match=1.0, freshness=0.5,
        engagement=0.5, rationale=[],
    )


def _video(video_id, title="Video"):
    return VideoCandidate(video_id=video_id, url="https://youtu.be/" + video_id, title=title)


def _work(title="Alt Konu"):
    subtopic = Subtopic(title=title, normalized_title=title.lower())
    return _SubtopicWork(subtopic=subtopic, query="q")


def _recommendation(video_id, subtopic="Alt Konu", title="Video"):
    from src.models import Recommendation

    return Recommendation(
        position=1,
        subtopic=subtopic,
        video=_video(video_id, title),
        why_selected="test",
        confidence_score=8.0,
        transcript_status="available",
        metadata_score=_score(),
    )


class DummyLLM:
    def __init__(self, note_text="ozet: nokta 1, nokta 2", fail_for=()):
        self.note_text = note_text
        self.fail_for = set(fail_for)
        self.calls = []

    def generate_subtopics(self, topic, language, max_items=6):
        return ["Alt Konu"]

    def generate_study_note(self, topic, subtopic, video_title, transcript_text, language):
        self.calls.append((topic, subtopic, video_title, transcript_text, language))
        if video_title in self.fail_for:
            raise ProviderTemporaryError("gecici hata (test)")
        return self.note_text


def test_video_without_transcript_gets_no_transcript_not_a_fabricated_note():
    """UYDURMA YASAGI: transkripti olmayan video icin not URETILMIYOR."""
    work = [_work()]
    rec = _recommendation("v1")
    request = PlaylistRequest(topic="Konu", filters=FilterOptions())
    llm = DummyLLM()

    notes = _generate_study_notes(
        AppConfig(gemini_api_key="x"), llm, request, work, [rec],
        {}, None, lambda *a, **k: None,
    )

    assert len(notes) == 1
    assert notes[0].status == "no_transcript"
    assert notes[0].content is None
    assert llm.calls == [], "transkripti olmayan video icin LLM HIC cagrilmamali"


def test_video_with_transcript_gets_a_generated_note():
    work = [_work("Kokusuz Kalman Filtresi")]
    rec = _recommendation("v1", subtopic="Kokusuz Kalman Filtresi", title="UKF Tutorial")
    request = PlaylistRequest(topic="Konu", filters=FilterOptions(language="tr"))
    llm = DummyLLM(note_text="ozet: UKF nedir")
    transcripts = {"v1": TranscriptResult(video_id="v1", status="available", source="x", text="transkript metni")}

    notes = _generate_study_notes(
        AppConfig(gemini_api_key="x"), llm, request, work, [rec],
        transcripts, None, lambda *a, **k: None,
    )

    assert notes[0].status == "available"
    assert notes[0].content == "ozet: UKF nedir"
    assert notes[0].video_id == "v1"
    topic, subtopic, video_title, transcript_text, language = llm.calls[0]
    assert topic == "Konu"
    assert subtopic == "Kokusuz Kalman Filtresi"
    assert video_title == "UKF Tutorial"
    assert transcript_text == "transkript metni"
    assert language == "tr"


def test_llm_failure_is_captured_not_raised():
    """Bir videonun notu patlarsa TUM calistirma DUSMEMELI."""
    work = [_work()]
    rec = _recommendation("v1", title="Patlayan Video")
    request = PlaylistRequest(topic="Konu", filters=FilterOptions())
    llm = DummyLLM(fail_for={"Patlayan Video"})
    transcripts = {"v1": TranscriptResult(video_id="v1", status="available", source="x", text="metin")}

    notes = _generate_study_notes(
        AppConfig(gemini_api_key="x"), llm, request, work, [rec],
        transcripts, None, lambda *a, **k: None,
    )

    assert notes[0].status == "failed"
    assert notes[0].content is None
    assert "gecici hata" in notes[0].error


def test_empty_transcript_text_is_treated_as_missing():
    """`status="available"` ama `text` bos ise yine not uydurulmamali."""
    work = [_work()]
    rec = _recommendation("v1")
    request = PlaylistRequest(topic="Konu", filters=FilterOptions())
    llm = DummyLLM()
    transcripts = {"v1": TranscriptResult(video_id="v1", status="available", source="x", text="")}

    notes = _generate_study_notes(
        AppConfig(gemini_api_key="x"), llm, request, work, [rec],
        transcripts, None, lambda *a, **k: None,
    )

    assert notes[0].status == "no_transcript"
    assert llm.calls == []


def test_subtopics_without_a_recommendation_are_skipped():
    """Video atanamamis alt konu icin not uretilmez (uretilecek bir sey yok)."""
    work = [_work("A"), _work("B")]
    rec_b = _recommendation("v2", subtopic="B")
    request = PlaylistRequest(topic="Konu", filters=FilterOptions())
    llm = DummyLLM()
    transcripts = {"v2": TranscriptResult(video_id="v2", status="available", source="x", text="metin")}

    notes = _generate_study_notes(
        AppConfig(gemini_api_key="x"), llm, request, work, [None, rec_b],
        transcripts, None, lambda *a, **k: None,
    )

    assert len(notes) == 1
    assert notes[0].subtopic == "B"


def test_concurrent_generation_does_not_corrupt_order():
    """Sonuclar TALEP SIRASIYLA donmeli, tamamlanma sirasiyla degil."""
    n = 12
    work = [_work("S" + str(i)) for i in range(n)]
    recs = [_recommendation("v" + str(i), subtopic="S" + str(i)) for i in range(n)]
    transcripts = {
        "v" + str(i): TranscriptResult(video_id="v" + str(i), status="available", source="x", text="metin-" + str(i))
        for i in range(n)
    }
    request = PlaylistRequest(topic="Konu", filters=FilterOptions())

    class SlowVaryingLLM(DummyLLM):
        def generate_study_note(self, topic, subtopic, video_title, transcript_text, language):
            import time
            index = int(video_title.split("-")[-1])
            time.sleep((n - index) * 0.002)
            return "not-" + transcript_text

    llm = SlowVaryingLLM()
    for i, rec in enumerate(recs):
        rec.video.title = "video-" + str(i)

    config = AppConfig(gemini_api_key="x", max_transcript_workers=6)
    notes = _generate_study_notes(
        config, llm, request, work, recs, transcripts, None, lambda *a, **k: None,
    )

    assert [note.video_id for note in notes] == ["v" + str(i) for i in range(n)]
    assert [note.content for note in notes] == ["not-metin-" + str(i) for i in range(n)]


def _install(monkeypatch, candidates, llm):
    monkeypatch.setattr(playlist_service, "create_llm_provider", lambda config: llm)
    monkeypatch.setattr(playlist_service, "search_candidates", lambda *a, **k: list(candidates))
    monkeypatch.setattr(
        playlist_service, "rank_candidates",
        lambda cands, t, s, f, **kw: [(c, _score()) for c in cands],
    )


def test_disabled_by_default_produces_no_study_notes(monkeypatch, tmp_path):
    """VARSAYILAN KAPALI: eski davranis degismemis olmali."""
    config = _config(tmp_path)
    llm = DummyLLM()
    _install(monkeypatch, [_video("v1", "Video")], llm)
    monkeypatch.setattr(
        playlist_service, "get_transcript",
        lambda *a, **k: TranscriptResult(video_id="v1", status="available", source="x", text="metin"),
    )

    result = playlist_service.build_playlist(config, PlaylistRequest(topic="Konu"))

    assert result.study_notes == []
    assert llm.calls == []


def test_enabled_produces_notes_end_to_end(monkeypatch, tmp_path):
    config = _config(tmp_path, enable_study_notes=True)
    llm = DummyLLM(note_text="ozet: ana fikir")
    _install(monkeypatch, [_video("v1", "Video")], llm)
    monkeypatch.setattr(
        playlist_service, "get_transcript",
        lambda *a, **k: TranscriptResult(video_id="v1", status="available", source="x", text="uzun transkript metni"),
    )

    result = playlist_service.build_playlist(config, PlaylistRequest(topic="Konu"))

    assert len(result.study_notes) == 1
    assert result.study_notes[0].status == "available"
    assert result.study_notes[0].content == "ozet: ana fikir"


def test_enabled_but_no_transcript_reports_honestly_end_to_end(monkeypatch, tmp_path):
    config = _config(tmp_path, enable_study_notes=True)
    llm = DummyLLM()
    _install(monkeypatch, [_video("v1", "Video")], llm)
    monkeypatch.setattr(
        playlist_service, "get_transcript",
        lambda *a, **k: TranscriptResult(video_id="v1", status="unavailable", source="none"),
    )

    result = playlist_service.build_playlist(config, PlaylistRequest(topic="Konu"))

    assert len(result.study_notes) == 1
    assert result.study_notes[0].status == "no_transcript"
    assert llm.calls == []


def test_failed_note_adds_a_visible_warning(monkeypatch, tmp_path):
    config = _config(tmp_path, enable_study_notes=True)
    llm = DummyLLM(fail_for={"Video"})
    _install(monkeypatch, [_video("v1", "Video")], llm)
    monkeypatch.setattr(
        playlist_service, "get_transcript",
        lambda *a, **k: TranscriptResult(video_id="v1", status="available", source="x", text="metin"),
    )

    result = playlist_service.build_playlist(config, PlaylistRequest(topic="Konu"))

    assert result.study_notes[0].status == "failed"
    assert any(
        "çalışma notu üretilemedi" in warning
        for warning in result.warnings
    )


def test_markdown_export_shows_all_three_statuses_distinctly():
    """Disa aktarilan Markdown'da UC durum da BIRBIRINDEN AYIRT EDILEBILIR olmali.

    "available" olmayan bir not icin bos veya sessiz gecmek, okuyucuya iceriğin
    orada olup olmadigini anlamasini zorlastirirdi.
    """
    from src.models import PlaylistResult, StudyNote
    from src.services.playlist_service import _render_markdown

    result = PlaylistResult(
        run_id="r1",
        topic="Konu",
        filters=FilterOptions(),
        subtopics=[],
        recommendations=[],
        study_notes=[
            StudyNote(subtopic="A", video_id="v1", status="available", content="ozet metni"),
            StudyNote(subtopic="B", video_id="v2", status="no_transcript"),
            StudyNote(subtopic="C", video_id="v3", status="failed", error="LLM zaman asimi"),
        ],
    )

    markdown = _render_markdown(result)

    assert "## Study Notes" in markdown
    assert "ozet metni" in markdown
    assert "No transcript was available" in markdown
    assert "LLM zaman asimi" in markdown


def test_asr_transcript_produces_study_note_with_source_and_backend():
    """ASR ile uretilen transkriptten calisma notu cikarilabilmeli ve kaynak etiketlenmeli."""
    work = [_work("Konu Başlığı")]
    rec = _recommendation("v_asr", subtopic="Konu Başlığı", title="ASR Video")
    request = PlaylistRequest(topic="Konu", filters=FilterOptions(language="tr"))
    llm = DummyLLM(note_text="ASR transkriptinden uretilmis calisma notu")
    transcripts = {
        "v_asr": TranscriptResult(
            video_id="v_asr",
            status="available",
            source="asr",
            backend="faster_whisper",
            text="bu video sesinden cikarilmis transkript metnidir",
        )
    }

    notes = _generate_study_notes(
        AppConfig(gemini_api_key="x"),
        llm,
        request,
        work,
        [rec],
        transcripts,
        None,
        lambda *a, **k: None,
    )

    assert len(notes) == 1
    assert notes[0].status == "available"
    assert notes[0].content == "ASR transkriptinden uretilmis calisma notu"
    assert notes[0].transcript_source == "asr"
    assert notes[0].transcript_backend == "faster_whisper"


def test_targeted_asr_fallback_for_missing_transcript_in_study_notes(tmp_path, monkeypatch):
    """Transkripti henuz cekilmemis secilen video icin ASR fallback devreye girip not uretebilmeli."""
    from src.storage import SQLiteStore

    config = _config(tmp_path, enable_asr_fallback=True)
    store = SQLiteStore(config.sqlite_path)
    work = [_work("Hedef Konu")]
    rec = _recommendation("v_targeted", subtopic="Hedef Konu", title="Targeted Video")
    request = PlaylistRequest(topic="Konu", filters=FilterOptions(language="tr"))
    llm = DummyLLM(note_text="Hedefli ASR ile not")

    # get_transcript cagrildiginda ASR sonucu dondurulsun
    monkeypatch.setattr(
        playlist_service,
        "get_transcript",
        lambda *a, **k: TranscriptResult(
            video_id="v_targeted",
            status="available",
            source="asr",
            backend="whisper_cpp",
            text="otomatik cikarilmis ASR transkripti",
        ),
    )

    transcripts = {}  # bos transkript sozlugu
    notes = _generate_study_notes(
        config,
        llm,
        request,
        work,
        [rec],
        transcripts,
        None,
        lambda *a, **k: None,
        store=store,
        run_id="run123",
        run_dir=tmp_path,
    )

    assert len(notes) == 1
    assert notes[0].status == "available"
    assert notes[0].content == "Hedefli ASR ile not"
    assert notes[0].transcript_source == "asr"
    assert notes[0].transcript_backend == "whisper_cpp"
    assert "v_targeted" in transcripts

