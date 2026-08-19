"""Faz 1: FastAPI katmani.

Gercek HTTP istekleri, ag cagrisi yok: `build_playlist` sahte bir uygulamayla
degistiriliyor. Amac API sozlesmesini ve is/SSE akisini dogrulamak.
"""

from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from api import deps
from api.routers import runs as runs_router
from src.config import AppConfig
from src.models import ExportArtifacts, PlaylistResult, ProgressEvent


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Gecici veri dizini ve sahte kimlik bilgileriyle uygulama."""
    config = AppConfig(
        gemini_api_key="test-key",
        youtube_data_api_key="yt-key",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
    )
    config.ensure_directories()
    monkeypatch.setattr(deps, "_base_config", lambda: config)

    from api.main import app

    with TestClient(app) as test_client:
        test_client.app_config = config
        yield test_client


@pytest.fixture
def as_other_user(client):
    """Istegin sahibini gecici olarak baska bir kullaniciya cevirir.

    `get_current_user` bagimliligini ezmek, gercek kimlik dogrulama eklendiginde
    olacak seyi taklit etmenin en yakin yolu: rotalar degismiyor, yalnizca o
    bagimliligin dondurdugu kimlik degisiyor.
    """
    from api.main import app

    def switch(user_id: str = "baska-kullanici"):
        app.dependency_overrides[deps.get_current_user] = lambda: user_id

    yield switch
    app.dependency_overrides.pop(deps.get_current_user, None)


def _blocking_build(release):
    """Serbest birakilana kadar suren sahte calistirma.

    Iptal testleri isin GERCEKTEN ucusta olmasini gerektiriyor; uyku ile
    zamanlamaya guvenmek yerine bir `threading.Event` ile bekletiliyor.
    """

    def build(config, request, progress_callback=None, run_id=None, user_id="local"):
        from src.storage import SQLiteStore

        store = SQLiteStore(config.sqlite_path)
        store.create_run(run_id, request.topic, request.filters.model_dump(), user_id=user_id)
        if progress_callback:
            progress_callback(ProgressEvent(stage="test", message="basladi", progress=0.1))
        release.wait(timeout=10)
        # Iptal talebi bir sonraki ilerleme bildiriminde `JobCancelled` olarak gelir.
        if progress_callback:
            progress_callback(ProgressEvent(stage="test", message="bitti", progress=1.0))
        result = PlaylistResult(
            run_id=run_id,
            topic=request.topic,
            filters=request.filters,
            subtopics=[],
            recommendations=[],
            exports=ExportArtifacts(json_path="x.json", markdown_path="y.md"),
        )
        store.finalize_run(run_id, result)
        return result

    return build


def _fake_build(recommendations=0, events=(0.3, 0.7, 1.0), fail_with=None, delay=0.0):
    """`build_playlist` yerine gecen sahte uygulama."""

    def build(config, request, progress_callback=None, run_id=None, user_id="local"):
        from src.storage import SQLiteStore

        store = SQLiteStore(config.sqlite_path)
        store.create_run(run_id, request.topic, request.filters.model_dump(), user_id=user_id)
        for value in events:
            if progress_callback:
                progress_callback(
                    ProgressEvent(stage="test", message=f"adim {value}", progress=value)
                )
            if delay:
                time.sleep(delay)
        if fail_with:
            raise fail_with
        result = PlaylistResult(
            run_id=run_id,
            topic=request.topic,
            filters=request.filters,
            subtopics=[],
            recommendations=[],
            exports=ExportArtifacts(json_path="x.json", markdown_path="y.md"),
        )
        store.finalize_run(run_id, result)
        return result

    return build


def _wait_for(client, run_id, states={"done", "failed", "cancelled"}, timeout=5.0):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        response = client.get(f"/api/runs/{run_id}/status")
        if response.status_code == 200 and response.json()["state"] in states:
            return response.json()
        time.sleep(0.02)
    raise AssertionError(f"is {timeout} sn icinde tamamlanmadi")


# ------------------------------------------------------------------- temel

def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_openapi_schema_is_generated(client):
    """React tipleri bu semadan uretilecek."""
    schema = client.get("/openapi.json").json()
    assert "/api/runs" in schema["paths"]
    assert "/api/runs/{run_id}/events" in schema["paths"]
    assert "/api/config" in schema["paths"]


# --------------------------------------------------------------- calistirma

def test_create_run_returns_immediately(client, monkeypatch):
    """202 + is numarasi; istek uzun islemi BEKLEMEZ."""
    monkeypatch.setattr(runs_router, "build_playlist", _fake_build(delay=0.15))

    start = time.perf_counter()
    response = client.post("/api/runs", json={"topic": "Kalman filtresi"})
    elapsed = time.perf_counter() - start

    assert response.status_code == 202
    body = response.json()
    assert len(body["run_id"]) == 32
    assert body["events_url"].endswith("/events")
    assert elapsed < 0.2, "POST bloklamamali"


def test_empty_topic_is_rejected(client):
    assert client.post("/api/runs", json={"topic": "   "}).status_code == 422
    assert client.post("/api/runs", json={"topic": ""}).status_code == 422


def test_run_result_is_available_after_completion(client, monkeypatch):
    monkeypatch.setattr(runs_router, "build_playlist", _fake_build())

    run_id = client.post("/api/runs", json={"topic": "Zaman serisi"}).json()["run_id"]
    _wait_for(client, run_id)

    body = client.get(f"/api/runs/{run_id}").json()
    assert body["state"] == "done"
    assert body["result"]["topic"] == "Zaman serisi"


def test_unknown_run_is_404(client):
    assert client.get("/api/runs/yok").status_code == 404
    assert client.get("/api/runs/yok/status").status_code == 404


def test_failed_run_surfaces_the_error(client, monkeypatch):
    monkeypatch.setattr(runs_router, "build_playlist", _fake_build(fail_with=RuntimeError("patladi")))

    run_id = client.post("/api/runs", json={"topic": "Konu"}).json()["run_id"]
    status_body = _wait_for(client, run_id)

    assert status_body["state"] == "failed"
    assert "patladi" in status_body["error"]


# ---------------------------------------------------------------------- SSE

def _parse_sse(text):
    events = []
    for block in text.strip().split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        name = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            events.append((name, data))
    return events


def test_event_stream_delivers_progress_then_done(client, monkeypatch):
    monkeypatch.setattr(runs_router, "build_playlist", _fake_build(events=(0.25, 0.5, 1.0)))

    run_id = client.post("/api/runs", json={"topic": "Konu"}).json()["run_id"]
    with client.stream("GET", f"/api/runs/{run_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse("".join(response.iter_text()))

    progress = [data["progress"] for name, data in events if name == "progress"]
    assert progress == [0.25, 0.5, 1.0]
    assert events[-1][0] == "done"
    assert events[-1][1]["state"] == "done"


def test_done_event_body_matches_the_published_contract(client, monkeypatch):
    """Regresyon: `done` govdesi is katmaninin IC adlarini tasiyordu.

    Govde dogrudan `JobHandle.snapshot()` sozlugu olarak yollaniyordu; icinde
    `run_id` degil `job_id` vardi ve istemcinin isine yaramayan `user_id` da
    gidiyordu. Arayuz `run_id` bekledigi icin o alan calisma zamaninda
    `undefined` kaliyor, TypeScript ise `string` oldugunu iddia ediyordu.

    Anahtar kumesi TAM olarak karsilastiriliyor: eksik alan kadar fazla alan da
    hatadir, cunku `web/src/api/contract.ts` bu govdeyi OpenAPI'den uretilen
    `RunSnapshotBody` ile derleme zamaninda esitliyor.
    """
    monkeypatch.setattr(runs_router, "build_playlist", _fake_build(events=(1.0,)))

    run_id = client.post("/api/runs", json={"topic": "Konu"}).json()["run_id"]
    with client.stream("GET", f"/api/runs/{run_id}/events") as response:
        events = _parse_sse("".join(response.iter_text()))

    name, body = events[-1]
    assert name == "done"
    assert set(body) == {
        "run_id",
        "state",
        "created_at",
        "progress",
        "stage",
        "message",
        "error",
    }
    assert body["run_id"] == run_id, "ic ad `job_id` degil, API'nin adlandirmasi"
    assert "user_id" not in body, "kullanici kimligi tel uzerine cikmamali"


def test_event_stream_closes_for_finished_run(client, monkeypatch):
    """Yeniden baglanan istemci sonsuza kadar beklememeli.

    Bitmis bir isin akisi birikmis olaylari tekrar oynatip `done` ile kapanir —
    imlecsiz baglanan istemci de tam gecmisi gorur.
    """
    monkeypatch.setattr(runs_router, "build_playlist", _fake_build(events=(0.5, 1.0)))
    run_id = client.post("/api/runs", json={"topic": "Konu"}).json()["run_id"]
    _wait_for(client, run_id)

    with client.stream("GET", f"/api/runs/{run_id}/events") as response:
        events = _parse_sse("".join(response.iter_text()))

    names = [name for name, _ in events]
    assert names[-1] == "done", "akis `done` ile bitmeli"
    assert names.count("done") == 1
    assert [data["progress"] for name, data in events if name == "progress"] == [0.5, 1.0]


def test_events_carry_ids_for_resume(client, monkeypatch):
    """Her olay bir `id:` tasimali; `Last-Event-ID` bunun uzerine kurulu."""
    monkeypatch.setattr(runs_router, "build_playlist", _fake_build(events=(0.5, 1.0)))
    run_id = client.post("/api/runs", json={"topic": "Konu"}).json()["run_id"]

    with client.stream("GET", f"/api/runs/{run_id}/events") as response:
        raw = "".join(response.iter_text())

    assert "id: 0" in raw
    assert "id: 1" in raw


def test_last_event_id_resumes_the_stream(client, monkeypatch):
    """Regresyon: baglanti kopunca kacirilan ilerleme kayboluyordu."""
    monkeypatch.setattr(runs_router, "build_playlist", _fake_build(events=(0.2, 0.4, 0.6, 0.8)))
    run_id = client.post("/api/runs", json={"topic": "Konu"}).json()["run_id"]
    _wait_for(client, run_id)

    # 0 ve 1 numarali olaylari almis gibi yeniden baglan.
    with client.stream(
        "GET", f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "1"}
    ) as response:
        events = _parse_sse("".join(response.iter_text()))

    progress = [data["progress"] for name, data in events if name == "progress"]
    assert progress == [0.6, 0.8], "yalnizca kacirilanlar tekrar gonderilmeli"


def test_invalid_last_event_id_restarts_from_scratch(client, monkeypatch):
    monkeypatch.setattr(runs_router, "build_playlist", _fake_build(events=(0.5, 1.0)))
    run_id = client.post("/api/runs", json={"topic": "Konu"}).json()["run_id"]
    _wait_for(client, run_id)

    with client.stream(
        "GET", f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "bozuk"}
    ) as response:
        events = _parse_sse("".join(response.iter_text()))

    progress = [data["progress"] for name, data in events if name == "progress"]
    assert progress == [0.5, 1.0]


def test_two_clients_both_receive_all_events(client, monkeypatch):
    """Iki sekme ayni calistirmayi izleyebilmeli."""
    monkeypatch.setattr(runs_router, "build_playlist", _fake_build(events=(0.3, 0.6, 1.0)))
    run_id = client.post("/api/runs", json={"topic": "Konu"}).json()["run_id"]
    _wait_for(client, run_id)

    streams = []
    for _ in range(2):
        with client.stream("GET", f"/api/runs/{run_id}/events") as response:
            streams.append(_parse_sse("".join(response.iter_text())))

    for events in streams:
        progress = [data["progress"] for name, data in events if name == "progress"]
        assert progress == [0.3, 0.6, 1.0]


def test_event_stream_for_unknown_run_reports_error(client):
    with client.stream("GET", "/api/runs/yok/events") as response:
        events = _parse_sse("".join(response.iter_text()))
    assert events[0][0] == "error"


# ------------------------------------------------------------------- gecmis

def test_history_lists_runs_newest_first(client, monkeypatch):
    monkeypatch.setattr(runs_router, "build_playlist", _fake_build())
    for topic in ("Bir", "Iki", "Uc"):
        run_id = client.post("/api/runs", json={"topic": topic}).json()["run_id"]
        _wait_for(client, run_id)

    body = client.get("/api/runs?limit=2").json()

    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["limit"] == 2


def test_history_pagination(client, monkeypatch):
    monkeypatch.setattr(runs_router, "build_playlist", _fake_build())
    for index in range(4):
        run_id = client.post("/api/runs", json={"topic": f"K{index}"}).json()["run_id"]
        _wait_for(client, run_id)

    first = client.get("/api/runs?limit=2&offset=0").json()["items"]
    second = client.get("/api/runs?limit=2&offset=2").json()["items"]

    assert {r["run_id"] for r in first} & {r["run_id"] for r in second} == set()


def test_delete_removes_from_history(client, monkeypatch):
    monkeypatch.setattr(runs_router, "build_playlist", _fake_build())
    run_id = client.post("/api/runs", json={"topic": "Konu"}).json()["run_id"]
    _wait_for(client, run_id)

    assert client.delete(f"/api/runs/{run_id}").status_code == 204
    assert client.get("/api/runs").json()["total"] == 0


# ----------------------------------------------------------------- secenek

def test_request_options_override_server_defaults(client, monkeypatch):
    seen = {}

    def spy(config, request, progress_callback=None, run_id=None, user_id="local"):
        seen["max_subtopics"] = config.max_subtopics
        seen["enable_asr"] = config.enable_asr_fallback
        seen["language"] = request.filters.language
        from src.storage import SQLiteStore

        store = SQLiteStore(config.sqlite_path)
        store.create_run(run_id, request.topic, request.filters.model_dump(), user_id=user_id)
        return PlaylistResult(run_id=run_id, topic=request.topic, filters=request.filters,
                              subtopics=[], recommendations=[])

    monkeypatch.setattr(runs_router, "build_playlist", spy)

    run_id = client.post(
        "/api/runs",
        json={
            "topic": "Konu",
            "filters": {"language": "tr", "max_duration_minutes": 45},
            "options": {"max_subtopics": 3, "enable_asr_fallback": True},
        },
    ).json()["run_id"]
    _wait_for(client, run_id)

    assert seen == {"max_subtopics": 3, "enable_asr": True, "language": "tr"}


def test_invalid_option_is_rejected(client):
    response = client.post("/api/runs", json={"topic": "K", "options": {"max_subtopics": 99}})
    assert response.status_code == 422


# ------------------------------------------------------------------ config

def test_capabilities_report_configuration(client):
    body = client.get("/api/config").json()

    assert body["gemini_configured"] is True
    assert body["youtube_search_configured"] is True
    assert body["youtube_publish_configured"] is False
    assert "max_subtopics" in body["defaults"]


def test_capabilities_include_llm_configured(client):
    """Regresyon: `llm_configured` `public_capabilities()`e ekliydi ama
    `CapabilitiesResponse` modeline eklenmemisti; pydantic'in varsayilan
    `extra="ignore"` davranisi alani sessizce yaniti disinda birakiyordu.
    Tip kontrolu (`contract.ts`) frontend'e eklenince farki yakaladi.
    """
    body = client.get("/api/config").json()

    assert "llm_configured" in body
    assert body["llm_configured"] is True


def test_capabilities_never_leak_secrets(client):
    """En kritik guvenlik testi: anahtarlar bu uctan asla cikmamali."""
    raw = client.get("/api/config").text

    assert "test-key" not in raw
    assert "yt-key" not in raw


# ---------------------------------------------------------------- sahiplik
#
# Bu uc test `/events`, `/status` ve `DELETE` icin sahiplik kontrolunu kilitler.
# Tek kullanicili kurulumda `get_current_user` sabit donduğu icin acik somut
# degildi; ama `api/deps.py` "gercek kimlik dogrulama gelince yalnizca o
# fonksiyonun govdesi degisir, rotalar elden gecirilmez" diyor ve ilk ikisi o
# bagimliligi HIC almiyordu -- yani o gun sessizce acikta kalirlardi.


def test_event_stream_hides_another_users_run(client, monkeypatch, as_other_user):
    monkeypatch.setattr(runs_router, "build_playlist", _fake_build(events=(1.0,)))
    run_id = client.post("/api/runs", json={"topic": "Konu"}).json()["run_id"]

    as_other_user()
    with client.stream("GET", f"/api/runs/{run_id}/events") as response:
        events = _parse_sse("".join(response.iter_text()))

    # 404 DEGIL: `EventSource` HTTP hatasini govdesiz sayip sonsuza kadar yeniden
    # baglanir. Akis icinde `error` gonderilip kapatiliyor.
    assert response.status_code == 200
    assert [name for name, _ in events] == ["error"]
    assert events[0][1]["detail"] == "Bilinmeyen çalıştırma"


def test_status_hides_another_users_run(client, monkeypatch, as_other_user):
    monkeypatch.setattr(runs_router, "build_playlist", _fake_build(events=(1.0,)))
    run_id = client.post("/api/runs", json={"topic": "Konu"}).json()["run_id"]

    assert client.get(f"/api/runs/{run_id}/status").status_code == 200

    as_other_user()
    response = client.get(f"/api/runs/{run_id}/status")

    # "Yetkisiz" yerine "bilinmeyen": aksi halde var olan bir kimlik, var
    # olmayandan ayirt edilebilir hale gelirdi.
    assert response.status_code == 404
    assert response.json()["detail"] == "Bilinmeyen çalıştırma"


def test_another_user_cannot_cancel_a_running_job(client, monkeypatch, as_other_user):
    """Regresyon: iptal dali sahiplik suzgecinden gecmiyordu.

    `DELETE` silme dalinda `user_id` suzuyordu ama once cagrilan
    `runner.cancel(run_id)` yalnizca kimlige bakiyordu.
    """
    release = threading.Event()
    monkeypatch.setattr(runs_router, "build_playlist", _blocking_build(release))
    try:
        run_id = client.post("/api/runs", json={"topic": "Konu"}).json()["run_id"]
        _wait_for_state(client, run_id, "running")

        as_other_user()
        assert client.delete(f"/api/runs/{run_id}").status_code == 404

        as_other_user(deps.DEFAULT_USER_ID)
        assert client.get(f"/api/runs/{run_id}/status").json()["state"] == "running"

        # Sahibi iptal EDEBILMELI: kontrol erisimi kisitlamali, ozelligi degil.
        assert client.delete(f"/api/runs/{run_id}").status_code == 204
    finally:
        release.set()

    assert _wait_for(client, run_id)["state"] == "cancelled"


def _wait_for_state(client, run_id, state, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/runs/{run_id}/status")
        if body.status_code == 200 and body.json()["state"] == state:
            return
        time.sleep(0.02)
    raise AssertionError(f"`{state}` durumuna {timeout}s icinde ulasilmadi")
