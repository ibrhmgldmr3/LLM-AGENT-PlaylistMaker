"""Cok kullanicili kurulumun temelleri: BYOK anahtarlari ve oturumlar.

Faz 1 kapsamı: kimlik (Google hesabi) ve kullanici basina API anahtari.
Calistirma/gecmis kapsamasi `test_run_history.py` ve `test_api.py` icinde.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.storage import SQLiteStore

KEY = "k" * 44


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / "app.db"), encryption_key=KEY)


def _raw(tmp_path, sql):
    return sqlite3.connect(tmp_path / "app.db").execute(sql).fetchall()


# ------------------------------------------------------------ BYOK anahtarlar

def test_user_credentials_are_scoped_per_user(store):
    store.save_user_credential("ali", "gemini_api_key", "ALI-KEY")
    store.save_user_credential("veli", "gemini_api_key", "VELI-KEY")

    assert store.get_user_credentials("ali")["gemini_api_key"] == "ALI-KEY"
    assert store.get_user_credentials("veli")["gemini_api_key"] == "VELI-KEY"
    assert store.get_user_credentials("bilinmeyen") == {}


def test_user_credentials_are_encrypted_at_rest(store, tmp_path):
    """Anahtarlar diskte DUZ METIN durmamali.

    Kullanicinin kendi API anahtarini emanet etmesini istiyoruz; veritabani
    dosyasi yedeklere, senkronize klasorlere ve hata raporlarina karisiyor.
    """
    store.save_user_credential("ali", "gemini_api_key", "COK-GIZLI-DEGER")

    rows = _raw(tmp_path, "SELECT value FROM user_credential")

    assert rows, "kayit yazilmamis"
    assert all("COK-GIZLI-DEGER" not in row[0] for row in rows)


def test_saving_the_same_name_replaces_it(store):
    store.save_user_credential("ali", "gemini_api_key", "ESKI")
    store.save_user_credential("ali", "gemini_api_key", "YENI")

    assert store.get_user_credentials("ali")["gemini_api_key"] == "YENI"


def test_deleting_one_credential_keeps_the_others(store):
    store.save_user_credential("ali", "gemini_api_key", "G")
    store.save_user_credential("ali", "youtube_data_api_key", "Y")

    assert store.delete_user_credential("ali", "gemini_api_key") is True
    assert store.delete_user_credential("ali", "gemini_api_key") is False
    assert set(store.get_user_credentials("ali")) == {"youtube_data_api_key"}


# ----------------------------------------------------------------- oturumlar

def test_session_round_trips(store):
    store.create_session("jeton", "ali", "ali@example.com", ttl_sec=3600)

    assert store.get_session("jeton") == {"user_id": "ali", "email": "ali@example.com"}


def test_unknown_token_has_no_session(store):
    assert store.get_session("hic-verilmemis") is None


def test_only_the_token_hash_is_stored(store, tmp_path):
    """Jetonun KENDISI veritabaninda durmamali.

    Oturum jetonu bir bearer sirri: onu goren istek yapabilir. Ozet
    saklandiginda veritabani sizsa bile oturumlar devralinamaz -- sizan
    ozetten jeton uretilemez.
    """
    store.create_session("cok-gizli-jeton", "ali", None, ttl_sec=3600)

    rows = _raw(tmp_path, "SELECT token_hash FROM session")

    assert rows
    assert all("cok-gizli-jeton" not in row[0] for row in rows)


def test_expired_session_is_rejected_and_cleaned_up(store, tmp_path):
    store.create_session("eski", "ali", None, ttl_sec=-1)

    assert store.get_session("eski") is None
    assert _raw(tmp_path, "SELECT 1 FROM session") == [], "suresi gecen kayit silinmeli"


def test_logout_removes_the_session(store):
    store.create_session("jeton", "ali", None, ttl_sec=3600)

    assert store.delete_session("jeton") is True
    assert store.get_session("jeton") is None
    assert store.delete_session("jeton") is False


# --------------------------------------------------- API: multi_user modu

@pytest.fixture
def multi_user_client(tmp_path, monkeypatch):
    """`auth_mode="multi_user"` ile ayaga kalkan uygulama."""
    from fastapi.testclient import TestClient

    from api import deps
    from src.config import AppConfig

    config = AppConfig(
        gemini_api_key="sunucu-anahtari",
        youtube_data_api_key="sunucu-yt",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "app.db"),
        secret_encryption_key=KEY,
        auth_mode="multi_user",
    )
    config.ensure_directories()
    monkeypatch.setattr(deps, "_base_config", lambda: config)

    from api.main import app

    with TestClient(app) as client:
        client.store = SQLiteStore(config.sqlite_path, encryption_key=KEY)
        yield client


def test_requests_without_a_session_are_rejected(multi_user_client):
    response = multi_user_client.get("/api/runs")

    assert response.status_code == 401
    assert "Oturum" in response.json()["detail"]


def test_a_valid_session_identifies_the_user(multi_user_client):
    multi_user_client.store.create_session("jeton", "google:123", "a@b.c", ttl_sec=3600)
    multi_user_client.cookies.set("map_session", "jeton")

    assert multi_user_client.get("/api/runs").status_code == 200


def test_expired_session_is_rejected(multi_user_client):
    multi_user_client.store.create_session("eski", "google:123", None, ttl_sec=-1)
    multi_user_client.cookies.set("map_session", "eski")

    assert multi_user_client.get("/api/runs").status_code == 401


def test_server_keys_are_not_used_as_a_fallback(multi_user_client):
    """Regresyon riski: anahtarini girmemis kullanici SESSIZCE sunucunun
    kotasini harcamamali.

    YouTube Data API kotasi proje basina gunde 10.000 birim, bir calistirma
    ~1.200 birim. Yedege dusulseydi ikinci kullanici gunu bitirirdi -- ve
    kurulum sahibi bunu ancak kota bitince fark ederdi.
    """
    multi_user_client.store.create_session("jeton", "google:123", None, ttl_sec=3600)
    multi_user_client.cookies.set("map_session", "jeton")

    response = multi_user_client.post("/api/runs", json={"topic": "Konu"})

    assert response.status_code == 400
    assert "anahtar" in response.json()["detail"].lower()


def test_a_user_with_their_own_key_can_start_a_run(multi_user_client, monkeypatch):
    from api.routers import runs as runs_router

    monkeypatch.setattr(
        runs_router, "build_playlist", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("dur"))
    )
    multi_user_client.store.create_session("jeton", "google:123", None, ttl_sec=3600)
    multi_user_client.store.save_user_credential("google:123", "gemini_api_key", "KENDI-ANAHTARI")
    multi_user_client.cookies.set("map_session", "jeton")

    response = multi_user_client.post("/api/runs", json={"topic": "Konu"})

    assert response.status_code == 202


def test_runs_are_isolated_between_sessions(multi_user_client):
    """Iki kullanici birbirinin gecmisini gormemeli."""
    store = multi_user_client.store
    store.create_run("kosu-ali", "Ali'nin konusu", {}, user_id="google:ali")
    store.create_session("ali-jetonu", "google:ali", None, ttl_sec=3600)
    store.create_session("veli-jetonu", "google:veli", None, ttl_sec=3600)

    multi_user_client.cookies.set("map_session", "ali-jetonu")
    ali = multi_user_client.get("/api/runs").json()
    multi_user_client.cookies.set("map_session", "veli-jetonu")
    veli = multi_user_client.get("/api/runs").json()

    assert [item["run_id"] for item in ali["items"]] == ["kosu-ali"]
    assert veli["items"] == []


# ------------------------------------------------------------- giris akisi

def test_id_token_payload_is_decoded():
    """Kimlik ID token'dan cikariliyor; `to_json()` onu tasimiyor."""
    import base64
    import json as _json

    from src.services.playlist_publish_service import decode_id_token

    payload = base64.urlsafe_b64encode(
        _json.dumps({"sub": "123", "email": "a@b.c"}).encode()
    ).decode().rstrip("=")  # Google dolgusuz gonderiyor

    claims = decode_id_token(f"basli.{payload}.imza")

    assert claims["sub"] == "123"
    assert claims["email"] == "a@b.c"


@pytest.mark.parametrize("bad", [None, "", "tekparca", "iki.parca"])
def test_broken_id_token_yields_no_claims(bad):
    """Bozuk jeton istisna DEGIL bos sozluk uretmeli; cagiran girisi reddediyor."""
    from src.services.playlist_publish_service import decode_id_token

    assert decode_id_token(bad) == {}


def test_start_needs_no_session(multi_user_client, monkeypatch):
    """Tavuk-yumurta: giris yapmak icin giris yapmis olmak GEREKMEZ."""
    from api.routers import auth as auth_router

    monkeypatch.setattr(
        auth_router, "build_authorization_url", lambda *a, **k: ("https://accounts.google.com/x", "v")
    )

    response = multi_user_client.get("/api/auth/youtube/start")

    assert response.status_code == 200
    assert response.json()["authorization_url"].startswith("https://accounts.google.com/")


def test_callback_creates_a_session_keyed_by_google_sub(multi_user_client, monkeypatch):
    from api.routers import auth as auth_router
    from src.services.playlist_publish_service import ExchangedToken

    monkeypatch.setattr(auth_router, "build_authorization_url", lambda *a, **k: ("https://x", "v"))
    monkeypatch.setattr(
        auth_router,
        "exchange_code_for_token",
        lambda *a, **k: ExchangedToken(
            token_json='{"token": "t"}', google_sub="9876", email="ali@example.com"
        ),
    )
    state = multi_user_client.get("/api/auth/youtube/start").json()["state"]

    response = multi_user_client.get(
        f"/api/auth/youtube/callback?code=kod&state={state}", follow_redirects=False
    )

    assert response.status_code == 307
    assert "map_session" in response.cookies
    # Jeton kalici kimlige baglanmali; e-posta degisebilir, `sub` degismez.
    assert multi_user_client.store.get_oauth_token("google:9876", "youtube") == '{"token": "t"}'


def test_callback_refuses_when_google_gave_no_identity(multi_user_client, monkeypatch):
    """`openid` izni verilmemisse oturum ACILMAMALI."""
    from api.routers import auth as auth_router
    from src.services.playlist_publish_service import ExchangedToken

    monkeypatch.setattr(auth_router, "build_authorization_url", lambda *a, **k: ("https://x", "v"))
    monkeypatch.setattr(
        auth_router,
        "exchange_code_for_token",
        lambda *a, **k: ExchangedToken(token_json="{}", google_sub=None),
    )
    state = multi_user_client.get("/api/auth/youtube/start").json()["state"]

    response = multi_user_client.get(
        f"/api/auth/youtube/callback?code=kod&state={state}", follow_redirects=False
    )

    assert response.status_code == 400


def test_me_reports_no_session(multi_user_client):
    body = multi_user_client.get("/api/auth/me").json()

    assert body == {"signed_in": False, "user_id": None, "email": None, "auth_required": True}


def test_me_reports_the_signed_in_user(multi_user_client):
    multi_user_client.store.create_session("jeton", "google:9876", "ali@example.com", ttl_sec=3600)
    multi_user_client.cookies.set("map_session", "jeton")

    body = multi_user_client.get("/api/auth/me").json()

    assert body["signed_in"] is True
    assert body["email"] == "ali@example.com"


def test_logout_revokes_the_session_on_the_server(multi_user_client):
    """Yalnizca cerezi silmek YETMEZ.

    Cerez silinip kayit dursaydi, jetonun bir kopyasini ele geciren biri
    oturumu kullanmaya devam ederdi. Sunucu tarafli oturumun varlik sebebi
    iptal edilebilir olmasi.
    """
    multi_user_client.store.create_session("jeton", "google:9876", None, ttl_sec=3600)
    multi_user_client.cookies.set("map_session", "jeton")

    assert multi_user_client.post("/api/auth/logout").status_code == 204
    assert multi_user_client.store.get_session("jeton") is None


# --------------------------------------------------------- anahtar uclari

def test_credentials_never_return_the_values(multi_user_client):
    """Deger bir kez yazilir, GERI OKUNAMAZ.

    Yalnizca "girilmis mi" bilgisi doniyor; boylece bir XSS ya da yanlis
    loglama anahtari disari tasiyamaz. Kullanici unuttuysa yenisini girer.
    """
    store = multi_user_client.store
    store.create_session("jeton", "google:1", None, ttl_sec=3600)
    store.save_user_credential("google:1", "gemini_api_key", "COK-GIZLI")
    multi_user_client.cookies.set("map_session", "jeton")

    body = multi_user_client.get("/api/credentials").json()

    assert "COK-GIZLI" not in multi_user_client.get("/api/credentials").text
    gemini = next(item for item in body["items"] if item["name"] == "gemini_api_key")
    assert gemini["configured"] is True
    assert "value" not in gemini


def test_saving_a_credential_takes_effect(multi_user_client):
    multi_user_client.store.create_session("jeton", "google:1", None, ttl_sec=3600)
    multi_user_client.cookies.set("map_session", "jeton")

    assert multi_user_client.put(
        "/api/credentials/gemini_api_key", json={"value": "yeni-anahtar"}
    ).status_code == 204
    assert multi_user_client.store.get_user_credentials("google:1")["gemini_api_key"] == "yeni-anahtar"


def test_unknown_credential_names_are_rejected(multi_user_client):
    """Beyaz liste: rastgele bir adla yapilandirmaya deger sokulmasin."""
    multi_user_client.store.create_session("jeton", "google:1", None, ttl_sec=3600)
    multi_user_client.cookies.set("map_session", "jeton")

    response = multi_user_client.put("/api/credentials/sqlite_path", json={"value": "/etc/passwd"})

    assert response.status_code == 404


def test_credentials_require_a_session(multi_user_client):
    assert multi_user_client.get("/api/credentials").status_code == 401
