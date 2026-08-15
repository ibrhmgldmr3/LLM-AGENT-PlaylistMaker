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
