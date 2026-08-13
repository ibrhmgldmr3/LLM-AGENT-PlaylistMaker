"""Paralel boru hattinin dogrulugunu koruyup korumadigini sinar."""

import threading
import time

from src.config import AppConfig
from src.models import MetadataScore, PlaylistRequest, TranscriptResult, VideoCandidate
from src.services import playlist_service


class DummyLLM:
    def __init__(self, items):
        self.items = items

    def generate_subtopics(self, topic, language, max_items=6):
        return self.items[:max_items]


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


def _fake_rank(candidates, topic, subtopic, filters):
    """Alt konuya DUYARLI sahte siralayici.

    Alt konuyu yok sayan bir sahte siralayici, havuzlama sonrasi butun alt
    konulara ayni satiri verir ve atama testleri anlamsizlasir.
    """
    scored = [(c, _score(9.0 if subtopic in c.title else 2.0)) for c in candidates]
    scored.sort(key=lambda pair: -pair[1].total)
    return scored


def _install(monkeypatch, subtopics, search_fn, transcript_fn=None, pool_for=None):
    monkeypatch.setattr(playlist_service, "GeminiLLMProvider", lambda config: DummyLLM(subtopics))
    monkeypatch.setattr(playlist_service, "search_candidates", search_fn)
    monkeypatch.setattr(playlist_service, "rank_candidates", _fake_rank)
    monkeypatch.setattr(
        playlist_service,
        "get_transcript",
        transcript_fn
        or (lambda *a, **k: TranscriptResult(video_id="x", status="unavailable", source="none")),
    )


def test_searches_actually_run_in_parallel(monkeypatch, tmp_path):
    """4 alt konu x 0.3 sn -> seri 1.2 sn, paralel ~0.3 sn."""
    subtopics = ["A", "B", "C", "D"]
    config = _config(tmp_path, max_search_workers=4)
    concurrent, peak, lock = [0], [0], threading.Lock()

    def slow_search(config_, store, query, filters, logger=None, notes=None):
        with lock:
            concurrent[0] += 1
            peak[0] = max(peak[0], concurrent[0])
        time.sleep(0.3)
        with lock:
            concurrent[0] -= 1
        vid = query.split()[-1]
        return [VideoCandidate(video_id=f"video-{vid}", url=f"https://youtu.be/{vid}", title=f"T{vid}")]

    _install(monkeypatch, subtopics, slow_search)

    start = time.perf_counter()
    result = playlist_service.build_playlist(config, PlaylistRequest(topic="Konu"))
    elapsed = time.perf_counter() - start

    assert peak[0] >= 2, "aramalar es zamanli calismadi"
    assert elapsed < 1.0, f"paralellik kazanc saglamadi ({elapsed:.2f} sn)"
    assert len(result.recommendations) == 4


def test_subtopic_order_is_preserved_despite_out_of_order_completion(monkeypatch, tmp_path):
    """Paralel tamamlanma sirasi degisse de cikti alt konu sirasini korumali."""
    subtopics = ["Alpha", "Beta", "Gamma"]
    config = _config(tmp_path, max_search_workers=3)
    # Alpha en yavas, Gamma en hizli tamamlanir.
    delays = {"Alpha": 0.30, "Beta": 0.15, "Gamma": 0.01}

    def staggered_search(config_, store, query, filters, logger=None, notes=None):
        name = query.split()[-1]
        time.sleep(delays[name])
        return [VideoCandidate(video_id=f"video-{name}", url=f"https://youtu.be/{name}", title=name)]

    _install(monkeypatch, subtopics, staggered_search)

    result = playlist_service.build_playlist(config, PlaylistRequest(topic="Konu"))

    assert [item.subtopic.title for item in result.subtopics] == ["Alpha", "Beta", "Gamma"]
    assert [rec.subtopic for rec in result.recommendations] == ["Alpha", "Beta", "Gamma"]
    assert [rec.position for rec in result.recommendations] == [1, 2, 3]


def test_transcripts_are_deduplicated_across_subtopics(monkeypatch, tmp_path):
    """Ayni video birden fazla alt konuda cikarsa transkript BIR kez cekilmeli."""
    subtopics = ["A", "B", "C"]
    config = _config(tmp_path, transcript_enrichment_top_k=1, max_transcript_workers=4)
    shared = [VideoCandidate(video_id="shared", url="https://youtu.be/shared", title="Ortak"),
              VideoCandidate(video_id="other", url="https://youtu.be/other", title="Diger")]
    fetches, lock = [], threading.Lock()

    def transcript(config_, store, candidate, run_dir, state, logger=None, preferred_language=None):
        with lock:
            fetches.append(candidate.video_id)
        return TranscriptResult(video_id=candidate.video_id, status="unavailable", source="none")

    _install(monkeypatch, subtopics, lambda *a, **k: list(shared), transcript_fn=transcript)

    playlist_service.build_playlist(config, PlaylistRequest(topic="Konu"))

    assert fetches.count("shared") == 1, f"tekrar cekildi: {fetches}"


def test_worker_failure_does_not_abort_the_run(monkeypatch, tmp_path):
    """Bir alt konunun aramasi patlarsa digerleri tamamlanmali.

    Havuzlama sayesinde patlayan alt konu da ortak havuzdan bir video alabilir;
    bu istenen davranis, ama kullaniciya durum bildirilmeli.
    """
    subtopics = ["Good1", "Bad", "Good2"]
    config = _config(tmp_path, max_search_workers=3)

    def flaky_search(config_, store, query, filters, logger=None, notes=None):
        name = query.split()[-1]
        if name == "Bad":
            raise RuntimeError("arama coktu")
        return [VideoCandidate(video_id=f"video-{name}", url=f"https://youtu.be/{name}", title=name)]

    _install(monkeypatch, subtopics, flaky_search)

    result = playlist_service.build_playlist(config, PlaylistRequest(topic="Konu"))

    # Basarili alt konular kendi videolarini almali.
    chosen = {rec.subtopic: rec.video.video_id for rec in result.recommendations}
    assert chosen["Good1"] == "video-Good1"
    assert chosen["Good2"] == "video-Good2"
    # Patlayan alt konu havuzdan beslendigi bildirilmis olmali.
    assert any("Bad" in warning and "havuz" in warning for warning in result.warnings)


def test_pooling_lets_every_subtopic_see_all_candidates(monkeypatch, tmp_path):
    """Regresyon: her alt konu yalnizca KENDI arama sonuclarini goruyordu."""
    subtopics = ["Alpha", "Beta"]
    config = _config(tmp_path, max_search_workers=2)

    def search(config_, store, query, filters, logger=None, notes=None):
        name = query.split()[-1]
        return [VideoCandidate(video_id=f"video-{name}", url=f"https://youtu.be/{name}", title=name)]

    _install(monkeypatch, subtopics, search)

    result = playlist_service.build_playlist(config, PlaylistRequest(topic="Konu"))

    # Iki aramadan 2 benzersiz video; her alt konu ikisini birden degerlendirmeli.
    assert all(item.candidates_considered == 2 for item in result.subtopics)


def test_transcript_worker_failure_is_isolated(monkeypatch, tmp_path):
    subtopics = ["A", "B"]
    config = _config(tmp_path, transcript_enrichment_top_k=1, max_transcript_workers=2)

    def transcript(config_, store, candidate, run_dir, state, logger=None, preferred_language=None):
        if candidate.video_id == "video-A":
            raise RuntimeError("transkript coktu")
        return TranscriptResult(video_id=candidate.video_id, status="available",
                                source="youtube_transcript_api", text="x" * 60)

    def search(config_, store, query, filters, logger=None, notes=None):
        name = query.split()[-1]
        return [VideoCandidate(video_id=f"video-{name}", url=f"https://youtu.be/{name}", title=name)]

    _install(monkeypatch, subtopics, search, transcript_fn=transcript)

    result = playlist_service.build_playlist(config, PlaylistRequest(topic="Konu"))

    # Iki oneri de uretilmeli; transkripti patlayan video metadata ile secilir.
    assert len(result.recommendations) == 2


def test_progress_events_are_monotonic_and_bounded(monkeypatch, tmp_path):
    """Paralel fazlarda ilerleme geri gitmemeli ve 0..1 disina cikmamali."""
    subtopics = ["A", "B", "C"]
    config = _config(tmp_path, max_search_workers=3, max_transcript_workers=3)

    def search(config_, store, query, filters, logger=None, notes=None):
        name = query.split()[-1]
        return [VideoCandidate(video_id=f"video-{name}", url=f"https://youtu.be/{name}", title=name)]

    _install(monkeypatch, subtopics, search)

    events = []
    playlist_service.build_playlist(
        config, PlaylistRequest(topic="Konu"), progress_callback=lambda e: events.append(e.progress)
    )

    assert events == sorted(events), f"ilerleme geri gitti: {events}"
    assert all(0.0 <= value <= 1.0 for value in events)
    assert events[-1] == 1.0


def test_single_subtopic_skips_thread_pool(monkeypatch, tmp_path):
    """Tek is varken thread havuzu acmanin anlami yok."""
    config = _config(tmp_path, max_search_workers=4)
    threads = set()

    def search(config_, store, query, filters, logger=None, notes=None):
        threads.add(threading.current_thread().name)
        return [VideoCandidate(video_id="v", url="https://youtu.be/v", title="V")]

    _install(monkeypatch, ["Only"], search)

    playlist_service.build_playlist(config, PlaylistRequest(topic="Konu"))

    assert threads == {"MainThread"}
