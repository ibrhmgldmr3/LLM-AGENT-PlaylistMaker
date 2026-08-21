"""Faz 3: OAuth redirect akisi.

`run_local_server` sunucuda tarayici aciyordu; web uygulamasinda imkansiz.
Bu testler yeni uc adimli akisi ve jetonun kullanici basina saklanmasini kilitler.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from api import deps
from api.routers import auth as auth_router
from src.services.playlist_publish_service import ExchangedToken
from api.routers import runs as runs_router
from src.config import AppConfig
from src.models import ExportArtifacts, FilterOptions, PlaylistResult, Recommendation, VideoCandidate
from src.models import MetadataScore
from src.storage import SQLiteStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    config = AppConfig(
        gemini_api_key="test-key",
        youtube_oauth_client_id="client-id",
        youtube_oauth_client_secret="client-secret",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
    )
    config.ensure_directories()
    monkeypatch.setattr(deps, "_base_config", lambda: config)
    auth_router._pending_states.clear()

    from api.main import app

    with TestClient(app) as test_client:
        test_client.store = SQLiteStore(config.sqlite_path)
        yield test_client


def _completed_run(store: SQLiteStore, run_id="r1", user_id="local", with_recommendation=True):
    video = VideoCandidate(video_id="v1", url="https://youtu.be/v1", title="Video")
    score = MetadataScore(
        total=8.0, title_relevance=3.0, description_relevance=1.0, channel_quality=1.0,
        duration_fit=1.0, difficulty_fit=0.5, language_match=1.0, freshness=0.3,
        engagement=0.2, rationale=[],
    )
    result = PlaylistResult(
        run_id=run_id,
        topic="Konu",
        filters=FilterOptions(),
        subtopics=[],
        recommendations=[
            Recommendation(
                position=1, subtopic="Alt", video=video, why_selected="çünkü",
                confidence_score=8.0, transcript_status="unavailable", metadata_score=score,
            )
        ] if with_recommendation else [],
        exports=ExportArtifacts(json_path="a.json", markdown_path="b.md"),
    )
    store.create_run(run_id, "Konu", {}, user_id=user_id)
    store.finalize_run(run_id, result)
    return result


# ------------------------------------------------------------------- durum

def test_status_reports_not_connected_initially(client):
    body = client.get("/api/auth/youtube/status").json()
    assert body["connected"] is False
    assert body["configured"] is True


def test_status_reports_unconfigured_without_client_credentials(tmp_path, monkeypatch):
    config = AppConfig(gemini_api_key="k", data_dir=str(tmp_path), sqlite_path=str(tmp_path / "a.db"))
    config.ensure_directories()
    monkeypatch.setattr(deps, "_base_config", lambda: config)
    from api.main import app

    with TestClient(app) as bare:
        assert bare.get("/api/auth/youtube/status").json()["configured"] is False


# ----------------------------------------------------------------- baslatma

def test_start_returns_google_consent_url(client, monkeypatch):
    monkeypatch.setattr(
        auth_router,
        "build_authorization_url",
        lambda config, redirect, state, **kw: (
            f"https://accounts.google.com/o/oauth2/auth?state={state}",
            "verifier-abc",
        ),
    )

    body = client.get("/api/auth/youtube/start").json()

    assert body["authorization_url"].startswith("https://accounts.google.com/")
    assert body["state"] in body["authorization_url"]


def test_start_registers_a_state_for_csrf(client, monkeypatch):
    monkeypatch.setattr(auth_router, "build_authorization_url", lambda c, r, s, **kw: ("https://x", "v"))
    state = client.get("/api/auth/youtube/start").json()["state"]
    assert state in auth_router._pending_states


def test_pending_states_are_capped(client, monkeypatch):
    """Bekleyen state sozlugu sinirsiz buyuyememeli.

    `/start` oturum GEREKTIRMIYOR -- gerektirseydi giris yapmak icin once giris
    yapmis olmak gerekirdi. Tek sinir TTL olsaydi, 10 dakikalik pencere icinde
    sozlugu istedigi kadar buyutebilen kimliksiz bir yol kalirdi.
    """
    monkeypatch.setattr(auth_router, "build_authorization_url", lambda c, r, s, **kw: ("https://x", "v"))
    monkeypatch.setattr(auth_router, "MAX_PENDING_STATES", 3)

    for _ in range(5):
        client.get("/api/auth/youtube/start")

    assert len(auth_router._pending_states) == 3


def test_capacity_eviction_keeps_the_newest_states(client, monkeypatch):
    """Tavan asilinca EN ESKI bekleyenler atilmali.

    Yon onemli: en yeniyi atmak, sozlugu doldurmayi basaran birinin tum yeni
    girisleri kilitlemesi demek olurdu. En eskiyi atarken az once tiklamis
    gercek kullanicinin kaydi -- en taze olan -- ayakta kaliyor.
    """
    monkeypatch.setattr(auth_router, "build_authorization_url", lambda c, r, s, **kw: ("https://x", "v"))
    monkeypatch.setattr(auth_router, "MAX_PENDING_STATES", 3)

    states = [client.get("/api/auth/youtube/start").json()["state"] for _ in range(5)]

    assert [state in auth_router._pending_states for state in states] == [
        False, False, True, True, True
    ]


# --------------------------------------------------------------------- PKCE

def test_flow_asks_for_a_pkce_verifier_explicitly(monkeypatch):
    """Regresyon: PKCE'yi kutuphanenin varsayilanina birakmak surume bagimliydi.

    `Flow.__init__` `autogenerate_code_verifier=True` diyor ama fabrika metotlari
    kwargs'tan pop ederken kendi varsayilanini dayatiyor: google-auth-oauthlib
    1.2.0'da `None` (dogrulayici URETILMIYOR), 1.4.0'da `True`. Ayni kod kurulu
    surume gore ya calisiyor ya `invalid_grant: Missing code verifier` donuyordu.

    Kurulan nesnenin bayragina bakmak YETMEZ: varsayilani True olan bir surumde
    kwarg silinse bile boyle bir iddia gecerdi. Bu yuzden bayragin fabrikaya
    ACIKCA gecildigini dogruluyoruz -- kurulu surumden bagimsiz tek kontrol bu.
    """
    from google_auth_oauthlib.flow import Flow

    from src.config import AppConfig
    from src.services.playlist_publish_service import _build_web_flow

    seen = {}
    original = Flow.from_client_config

    def spy(client_config, scopes, **kwargs):
        seen.update(kwargs)
        return original(client_config, scopes, **kwargs)

    monkeypatch.setattr(Flow, "from_client_config", spy)

    config = AppConfig(
        gemini_api_key="x",
        youtube_oauth_client_id="id",
        youtube_oauth_client_secret="secret",
    )
    _build_web_flow(config, "http://localhost:8000/api/auth/youtube/callback")

    assert seen.get("autogenerate_code_verifier") is True


def test_real_authorization_url_carries_pkce(client):
    """Kutuphane PKCE'yi varsayilan olarak aciyor; dogrulayici geri gelmeli."""
    from urllib.parse import parse_qsl, urlparse

    body = client.get("/api/auth/youtube/start").json()
    params = dict(parse_qsl(urlparse(body["authorization_url"]).query))

    assert params.get("code_challenge_method") == "S256"
    assert params.get("code_challenge")
    # Dogrulayici sunucu tarafinda saklanmis olmali, URL'de DEGIL.
    _user, verifier, _expiry = auth_router._pending_states[body["state"]]
    assert verifier
    assert verifier not in body["authorization_url"]


def test_code_verifier_reaches_the_token_exchange(client, monkeypatch):
    """Regresyon: `invalid_grant: Missing code verifier`.

    `/start` ve `/callback` ayri `Flow` nesneleri kuruyor; dogrulayici yalnizca
    ilkinde yasadigi icin kaybolup Google tarafindan reddediliyordu.
    """
    seen = {}

    def spy(config, redirect_uri, code, code_verifier=None, **kw):
        seen["code"] = code
        seen["verifier"] = code_verifier
        return ExchangedToken(token_json='{"token": "ok"}')

    monkeypatch.setattr(auth_router, "exchange_code_for_token", spy)

    body = client.get("/api/auth/youtube/start").json()
    state = body["state"]
    expected_verifier = auth_router._pending_states[state][1]

    client.get(f"/api/auth/youtube/callback?code=kod&state={state}", follow_redirects=False)

    assert seen["code"] == "kod"
    assert seen["verifier"] == expected_verifier, "dogrulayici callback'e tasinmali"
    assert seen["verifier"] is not None


# ------------------------------------------------------------------ callback

def test_callback_stores_token_and_redirects(client, monkeypatch):
    monkeypatch.setattr(auth_router, "build_authorization_url", lambda c, r, s, **kw: ("https://x", "v"))
    monkeypatch.setattr(
        auth_router,
        "exchange_code_for_token",
        lambda config, redirect, code, code_verifier=None, **kw: ExchangedToken(
            token_json='{"token": "abc"}'
        ),
    )
    state = client.get("/api/auth/youtube/start").json()["state"]

    response = client.get(
        f"/api/auth/youtube/callback?code=kod&state={state}", follow_redirects=False
    )

    assert response.status_code in (302, 307)
    assert "youtube_auth=ok" in response.headers["location"]
    assert client.store.get_oauth_token("local", "youtube") == '{"token": "abc"}'


def test_callback_rejects_unknown_state(client):
    """CSRF korumasi: akisi biz baslatmis olmaliyiz."""
    response = client.get("/api/auth/youtube/callback?code=kod&state=sahte")
    assert response.status_code == 400


def test_state_is_single_use(client, monkeypatch):
    monkeypatch.setattr(auth_router, "build_authorization_url", lambda c, r, s, **kw: ("https://x", "v"))
    monkeypatch.setattr(
        auth_router,
        "exchange_code_for_token",
        lambda c, r, code, code_verifier=None, **kw: ExchangedToken(token_json="{}"),
    )
    state = client.get("/api/auth/youtube/start").json()["state"]

    client.get(f"/api/auth/youtube/callback?code=k&state={state}", follow_redirects=False)
    second = client.get(f"/api/auth/youtube/callback?code=k&state={state}")

    assert second.status_code == 400


def test_callback_handles_user_denial(client):
    response = client.get("/api/auth/youtube/callback?error=access_denied", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "youtube_auth=error" in response.headers["location"]


def test_disconnect_removes_the_token(client):
    client.store.save_oauth_token("local", "youtube", "{}")
    assert client.delete("/api/auth/youtube").status_code == 204
    assert client.store.get_oauth_token("local", "youtube") is None
    assert client.delete("/api/auth/youtube").status_code == 404


# ---------------------------------------------------------------- yayinlama

def test_publish_requires_a_connected_account(client):
    _completed_run(client.store)
    response = client.post("/api/runs/r1/publish")
    assert response.status_code == 401


def test_publish_uses_the_stored_token(client, monkeypatch):
    _completed_run(client.store)
    client.store.save_oauth_token("local", "youtube", '{"token": "abc"}')
    seen = {}

    def fake_publish(config, result, token_json=None, on_token_refresh=None, logger=None):
        seen["token"] = token_json
        from src.services.playlist_publish_service import PublishResult

        return PublishResult(url="https://youtube.com/playlist?list=X", added=1, warnings=[])

    monkeypatch.setattr(runs_router, "create_youtube_playlist", fake_publish)

    body = client.post("/api/runs/r1/publish").json()

    assert seen["token"] == '{"token": "abc"}', "saklanan jeton kullanilmali"
    assert body["url"].endswith("list=X")
    assert body["added"] == 1


def test_publish_persists_the_url_on_the_run(client, monkeypatch):
    _completed_run(client.store)
    client.store.save_oauth_token("local", "youtube", "{}")
    from src.services.playlist_publish_service import PublishResult

    monkeypatch.setattr(
        runs_router,
        "create_youtube_playlist",
        lambda *a, **k: PublishResult(url="https://yt/X", added=1, warnings=["bir uyarı"]),
    )

    client.post("/api/runs/r1/publish")

    stored = client.store.get_run("r1")
    assert stored.published_playlist_url == "https://yt/X"
    assert "bir uyarı" in stored.warnings


def test_refreshed_token_is_written_back(client, monkeypatch):
    """Yenilenen jeton kaybolmamali; aksi halde her yayinlamada yenileme gerekir."""
    _completed_run(client.store)
    client.store.save_oauth_token("local", "youtube", '{"old": true}')
    from src.services.playlist_publish_service import PublishResult

    def fake_publish(config, result, token_json=None, on_token_refresh=None, logger=None):
        on_token_refresh('{"fresh": true}')
        return PublishResult(url="https://yt/X", added=1, warnings=[])

    monkeypatch.setattr(runs_router, "create_youtube_playlist", fake_publish)

    client.post("/api/runs/r1/publish")

    assert json.loads(client.store.get_oauth_token("local", "youtube")) == {"fresh": True}


def test_publish_rejects_incomplete_run(client):
    client.store.save_oauth_token("local", "youtube", "{}")
    client.store.create_run("r2", "Konu", {}, user_id="local")
    assert client.post("/api/runs/r2/publish").status_code == 409


def test_publish_rejects_run_without_recommendations(client):
    _completed_run(client.store, run_id="r3", with_recommendation=False)
    client.store.save_oauth_token("local", "youtube", "{}")
    assert client.post("/api/runs/r3/publish").status_code == 409


def test_publish_respects_ownership(client):
    _completed_run(client.store, run_id="r4", user_id="baskasi")
    client.store.save_oauth_token("local", "youtube", "{}")
    assert client.post("/api/runs/r4/publish").status_code == 404


def test_tokens_are_scoped_per_user(client):
    client.store.save_oauth_token("ali", "youtube", '{"a": 1}')
    client.store.save_oauth_token("veli", "youtube", '{"v": 1}')
    assert client.store.get_oauth_token("ali", "youtube") == '{"a": 1}'
    assert client.store.get_oauth_token("veli", "youtube") == '{"v": 1}'
