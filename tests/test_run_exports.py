"""Cikti indirme ucu: `GET /api/runs/{id}/export/{artifact}`.

Bu ucun HIC testi yoktu. Diskten `FileResponse` ile okuyordu; artik ciktiyi
sonuctan yeniden uretiyor (bkz. `src/services/playlist_export.py`). Degisiklik
iki sey kazandiriyor ve ikisi de asagida kilitleniyor:

  * Indirmeyi karsilayan surecin, dosyayi ureten surec olmasi gerekmiyor.
  * Calistirma dizini yok olsa bile indirme calisiyor (eskiden 410 donuyordu).
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.config import AppConfig, settings
from src.models import (
    ExportArtifacts,
    FilterOptions,
    MetadataScore,
    PlaylistResult,
    Recommendation,
    VideoCandidate,
)
from src.services.playlist_export import render_markdown
from src.storage import SQLiteStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    config = AppConfig(
        gemini_api_key="test-key",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
    )
    config.ensure_directories()
    monkeypatch.setattr(settings, "base_config", lambda: config)

    from api.main import app

    with TestClient(app) as test_client:
        test_client.app_config = config
        yield test_client


def _seed(client, run_id="r1", user_id="local") -> PlaylistResult:
    """Tamamlanmis bir calistirma yazar ve sonucunu doner."""
    store = SQLiteStore(client.app_config.sqlite_path)
    video = VideoCandidate(video_id="v1", url="https://youtu.be/v1", title="Kalman filtresi")
    score = MetadataScore(
        total=8.0, title_relevance=3.0, description_relevance=1.0, channel_quality=1.0,
        duration_fit=1.0, difficulty_fit=0.5, language_match=1.0, freshness=0.3,
        engagement=0.2, rationale=[],
    )
    result = PlaylistResult(
        run_id=run_id,
        topic="Kalman filtresi",
        filters=FilterOptions(),
        subtopics=[],
        recommendations=[
            Recommendation(
                position=1, subtopic="Giriş", video=video, why_selected="temel anlatım",
                confidence_score=8.0, transcript_status="available", metadata_score=score,
            )
        ],
        exports=ExportArtifacts(json_path="/yok/result.json", markdown_path="/yok/study_plan.md"),
    )
    store.create_run(run_id, "Kalman filtresi", {}, user_id=user_id)
    store.finalize_run(run_id, result)
    return result


def test_json_export_returns_the_stored_result(client):
    result = _seed(client)

    response = client.get("/api/runs/r1/export/json")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert json.loads(response.text)["topic"] == result.topic


def test_markdown_export_matches_the_renderer(client):
    """Indirilen icerik, diske yazilanla AYNI bicimleyiciden gelmeli."""
    result = _seed(client)

    response = client.get("/api/runs/r1/export/markdown")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.text == render_markdown(result)


@pytest.mark.parametrize(
    "artifact, filename",
    [("json", "result.json"), ("markdown", "study_plan.md")],
)
def test_download_carries_a_filename(client, artifact, filename):
    """`FileResponse` bu basligi kendi kuruyordu; icerik artik bellekten geliyor."""
    _seed(client)

    response = client.get(f"/api/runs/r1/export/{artifact}")

    assert filename in response.headers["content-disposition"]


def test_export_does_not_need_the_run_directory(client):
    """ASIL KAZANC.

    Sonuc SQLite'ta durdugu surece indirme calismali. `_seed` bilerek VAR
    OLMAYAN yollar yaziyor (`/yok/...`): eski surum diskten okudugu icin bu
    durumda "Dosya sunucudan silinmis" (410) donuyordu -- oysa veri yerindeydi.
    Ayni sebeple indirmeyi karsilayan surecin dosyayi ureten surec olmasi da
    gerekmiyor.
    """
    _seed(client)

    for artifact in ("json", "markdown"):
        response = client.get(f"/api/runs/r1/export/{artifact}")
        assert response.status_code == 200, artifact
        assert response.text


def test_unknown_artifact_is_404(client):
    _seed(client)

    assert client.get("/api/runs/r1/export/pdf").status_code == 404


def test_unknown_run_is_404(client):
    assert client.get("/api/runs/yok/export/json").status_code == 404


def test_another_users_run_is_404_not_403(client):
    """403 vermek calistirmanin VAR OLDUGUNU sizdirirdi."""
    _seed(client, user_id="baskasi")

    assert client.get("/api/runs/r1/export/json").status_code == 404


def test_incomplete_run_is_409(client):
    store = SQLiteStore(client.app_config.sqlite_path)
    store.create_run("r2", "Konu", {}, user_id="local")

    assert client.get("/api/runs/r2/export/json").status_code == 409
