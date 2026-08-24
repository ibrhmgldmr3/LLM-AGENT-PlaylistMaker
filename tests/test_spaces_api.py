"""Ogrenme alani API'si: sahiplik, yukleme sinirlari ve silme kaskadi.

Gercek HTTP istekleri, ag cagrisi yok: LLM saglayicisi sahte bir uygulamayla
degistiriliyor. Amac API sozlesmesini ve yetki sinirlarini dogrulamak.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from api import deps
from api.routers import spaces as spaces_router
from src.config import AppConfig
from src.models import (
    FilterOptions,
    MetadataScore,
    PlaylistResult,
    Recommendation,
    VideoCandidate,
)
from src.storage import SQLiteStore


class FakeLLM:
    """Sahte saglayici.

    `embed` ICERIGE DUYARLI olmali: her metne ayni vektoru donduren bir sahte,
    alakasiz bir soruyu da kaynaklara %100 benzer gosterir ve kacinma kapisini
    sessizce devre disi birakir -- yani testin olctugu sey ortadan kalkar.
    Kucuk bir sozluk uzerinde kelime sayimi yeterli.
    """

    VOCABULARY = ("kovaryans", "matris", "entropi", "hava", "kalman")

    def embed(self, texts):
        return [
            [float(text.lower().count(word)) for word in self.VOCABULARY] for text in texts
        ]

    def answer_from_context(self, question, chunks, language):
        return {
            "answered": True,
            "answer": "Cevap.",
            "used_chunk_ids": [chunks[0]["chunk_id"]],
            "missing": "",
        }


@pytest.fixture
def client(tmp_path, monkeypatch):
    config = AppConfig(
        gemini_api_key="test-key",
        youtube_data_api_key="yt-key",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
        enable_rag=True,
        max_spaces_per_user=2,
        max_documents_per_space=2,
        max_upload_bytes=2048,
    )
    config.ensure_directories()
    monkeypatch.setattr(deps, "_base_config", lambda: config)
    monkeypatch.setattr(spaces_router, "create_rag_llm_provider", lambda _config: FakeLLM())

    from api.main import app

    with TestClient(app) as test_client:
        test_client.app_config = config
        yield test_client


@pytest.fixture
def as_other_user():
    from api.main import app

    def switch(user_id: str = "baska-kullanici"):
        app.dependency_overrides[deps.get_current_user] = lambda: user_id

    yield switch
    app.dependency_overrides.pop(deps.get_current_user, None)


def _create(client, name="Kalman") -> str:
    response = client.post("/api/spaces", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["space_id"]


def _upload(client, space_id, filename="ders.txt", content=b"Kovaryans matrisi kucultur."):
    """Dokuman yukler ve INDEKSLENMESINI bekler; kaynak satirini doner.

    Yukleme ucu 202 donuyor ve parcalama/gomme ARKA PLAN isinde calisiyor:
    kaynak satiri senkron yaziliyor ama PARCALAR yazilmiyor. Beklemeden devam
    eden bir test tek basina kostugunda geciyor, tum takim altinda dusuyordu --
    yani olctugu sey degil, makinenin o anki yuku belirliyordu.

    Ilerlemeyi SSE ile izlemek de mumkundu; testin ilgilendigi sey akis degil
    SONUC oldugu icin durum yoklamak hem daha kisa hem de neyin beklendigini
    dogrudan soyluyor.
    """
    response = client.post(
        f"/api/spaces/{space_id}/sources/document",
        files={"file": (filename, content, "text/plain")},
    )
    assert response.status_code == 202, response.text
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        sources = client.get(f"/api/spaces/{space_id}").json()["sources"]
        target = next((item for item in sources if item["title"] == filename), None)
        if target and target["status"] != "pending":
            return target
        time.sleep(0.02)
    raise AssertionError(f"{filename} 10 saniye icinde indekslenmedi")


def _seed_run(client, run_id="run1", user_id="local") -> str:
    """Tamamlanmis bir calistirma yazar (transkriptsiz videoyla)."""
    store = SQLiteStore(client.app_config.sqlite_path)
    filters = FilterOptions()
    store.create_run(run_id, "Kalman filtresi", filters.model_dump(), user_id=user_id)
    video = VideoCandidate(
        video_id="abc123def45",
        url="https://www.youtube.com/watch?v=abc123def45",
        title="Kalman filtresi anlatimi",
    )
    result = PlaylistResult(
        run_id=run_id,
        topic="Kalman filtresi",
        filters=filters,
        subtopics=[],
        recommendations=[
            Recommendation(
                position=1,
                subtopic="Giris",
                video=video,
                why_selected="test",
                confidence_score=0.9,
                transcript_status="unavailable",
                metadata_score=MetadataScore(
                    total=1.0,
                    title_relevance=1.0,
                    description_relevance=0.0,
                    channel_quality=0.0,
                    duration_fit=0.0,
                    language_match=0.0,
                    freshness=0.0,
                    engagement=0.0,
                ),
            )
        ],
    )
    store.finalize_run(run_id, result)
    return run_id


# ------------------------------------------------------------------- alanlar


def test_create_and_list_space(client):
    space_id = _create(client)

    listing = client.get("/api/spaces").json()

    assert listing["total"] == 1
    assert listing["items"][0]["space_id"] == space_id
    assert listing["items"][0]["source_count"] == 0


def test_space_limit_is_enforced(client):
    _create(client, "bir")
    _create(client, "iki")

    response = client.post("/api/spaces", json={"name": "uc"})

    assert response.status_code == 429
    assert "en fazla 2" in response.json()["detail"].lower()


def test_another_users_space_is_a_404_not_a_403(client, as_other_user):
    """403 vermek alanin VAR OLDUGUNU sizdirirdi."""
    space_id = _create(client)
    as_other_user()

    assert client.get(f"/api/spaces/{space_id}").status_code == 404
    assert client.delete(f"/api/spaces/{space_id}").status_code == 404
    assert client.post(f"/api/spaces/{space_id}/ask", json={"question": "x"}).status_code == 404


def test_unknown_space_is_404(client):
    assert client.get("/api/spaces/yok").status_code == 404


def test_rag_disabled_is_reported_clearly(client, monkeypatch):
    """Sessizce bos sonuc donmek, ozelligi BOZUK gosterirdi."""
    client.app_config.enable_rag = False

    response = client.post("/api/spaces", json={"name": "x"})

    assert response.status_code == 503
    assert "ENABLE_RAG" in response.json()["detail"]


def test_capabilities_report_rag_availability(client):
    body = client.get("/api/config").json()

    assert body["rag_available"] is True


# ------------------------------------------------------------------ yukleme


def test_document_upload_is_accepted_and_indexed(client):
    space_id = _create(client)

    source = _upload(
        client, space_id, content=b"Kovaryans matrisi kucultur. Kalman filtresi."
    )

    assert source["status"] == "indexed"
    assert source["chunk_count"] > 0
    detail = client.get(f"/api/spaces/{space_id}").json()
    assert detail["source_count"] == 1
    assert detail["chunk_count"] > 0


def test_unsupported_file_type_is_rejected(client):
    space_id = _create(client)

    response = client.post(
        f"/api/spaces/{space_id}/sources/document",
        files={"file": ("arsiv.zip", b"PK\x03\x04", "application/zip")},
    )

    assert response.status_code == 415
    assert client.get(f"/api/spaces/{space_id}").json()["source_count"] == 0


def test_oversized_upload_is_rejected_and_leaves_no_file(client):
    """Sinir OKUMA SIRASINDA uygulaniyor; `Content-Length` istemcinin beyani."""
    space_id = _create(client)

    response = client.post(
        f"/api/spaces/{space_id}/sources/document",
        files={"file": ("buyuk.txt", b"a" * 5000, "text/plain")},
    )

    assert response.status_code == 413
    assert client.get(f"/api/spaces/{space_id}").json()["source_count"] == 0
    upload_dir = client.app_config.runs_dir.parent / "uploads"
    leftovers = list(upload_dir.rglob("*.txt")) if upload_dir.exists() else []
    assert leftovers == []


def test_empty_upload_is_rejected(client):
    space_id = _create(client)

    response = client.post(
        f"/api/spaces/{space_id}/sources/document",
        files={"file": ("bos.txt", b"", "text/plain")},
    )

    assert response.status_code == 400


def test_document_limit_is_enforced(client):
    space_id = _create(client)
    for index in range(2):
        _upload(client, space_id, filename=f"d{index}.txt")

    response = client.post(
        f"/api/spaces/{space_id}/sources/document",
        files={"file": ("d3.txt", b"Kovaryans matrisi.", "text/plain")},
    )

    assert response.status_code == 429


def test_stored_filename_is_generated_not_taken_from_the_user(client):
    """Kullanicinin verdigi ad yalnizca GORUNEN BASLIK."""
    space_id = _create(client)

    source = _upload(client, space_id, filename="../../kotu.txt")
    assert "/" not in source["ref_id"] and "\\" not in source["ref_id"]
    assert source["ref_id"].endswith(".txt")


# ---------------------------------------------------------------- calistirma


def test_adding_an_unknown_run_is_404(client):
    space_id = _create(client)

    response = client.post(f"/api/spaces/{space_id}/sources/run", json={"run_id": "yok"})

    assert response.status_code == 404


def test_adding_another_users_run_is_404(client, as_other_user):
    space_id = _create(client)
    _seed_run(client, user_id="baskasi")

    response = client.post(f"/api/spaces/{space_id}/sources/run", json={"run_id": "run1"})

    assert response.status_code == 404


def test_adding_a_run_returns_a_job(client, monkeypatch):
    space_id = _create(client)
    _seed_run(client)
    # Transkript cekimi ag istiyor; kaynak "metin yok" olarak isaretlenmeli.
    monkeypatch.setattr(
        spaces_router.rag_service,
        "ingest_run",
        lambda *a, **k: spaces_router.rag_service.IngestReport(added=1, skipped_no_text=["v"]),
    )

    response = client.post(f"/api/spaces/{space_id}/sources/run", json={"run_id": "run1"})

    assert response.status_code == 202
    assert response.json()["space_id"] == space_id


# --------------------------------------------------------------------- soru


def test_ask_returns_an_answer_with_citations(client):
    space_id = _create(client)
    _upload(client, space_id)

    body = client.post(f"/api/spaces/{space_id}/ask", json={"question": "kovaryans"}).json()

    assert body["answered"] is True
    assert body["citations"][0]["title"] == "ders.txt"


def test_not_found_answer_is_a_200_not_an_error(client):
    """`answered=False` ozelligin VAADI; HTTP hatasi yapmak onu ariza gosterirdi."""
    space_id = _create(client)
    _upload(client, space_id)

    response = client.post(
        f"/api/spaces/{space_id}/ask", json={"question": "bugun hava nasil"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answered"] is False
    assert body["searched_sources"] == 1
    assert body["reason"]


def test_blank_question_is_rejected_by_validation(client):
    space_id = _create(client)

    assert client.post(f"/api/spaces/{space_id}/ask", json={"question": ""}).status_code == 422


# --------------------------------------------------------------------- silme


def test_deleting_a_space_removes_content_and_uploaded_files(client):
    space_id = _create(client)
    _upload(client, space_id)
    store = SQLiteStore(client.app_config.sqlite_path)
    assert store.count_chunks(space_id) > 0

    assert client.delete(f"/api/spaces/{space_id}").status_code == 204

    assert store.count_chunks(space_id) == 0
    assert store.list_sources(space_id) == []
    upload_dir = client.app_config.runs_dir.parent / "uploads"
    assert not list(upload_dir.rglob("*.txt")) if upload_dir.exists() else True


def test_deleting_one_source_leaves_the_space(client):
    space_id = _create(client)
    _upload(client, space_id, filename="bir.txt")
    _upload(client, space_id, filename="iki.txt", content=b"Entropi tanimi.")
    sources = client.get(f"/api/spaces/{space_id}").json()["sources"]

    response = client.delete(f"/api/spaces/{space_id}/sources/{sources[0]['source_id']}")

    assert response.status_code == 204
    remaining = client.get(f"/api/spaces/{space_id}").json()
    assert remaining["source_count"] == 1


def test_deleting_an_unknown_source_is_404(client):
    space_id = _create(client)

    assert client.delete(f"/api/spaces/{space_id}/sources/doc:yok.txt").status_code == 404
