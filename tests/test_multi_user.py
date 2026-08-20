"""Cok kullanicili kurulumun temelleri: oturumlar ve paylasimli anahtarlar.

Faz 1 kapsamı: kimlik (Google hesabi). LLM/YouTube arama anahtarlari BYOK
DEGIL, sunucudan PAYLASIMLI gelir (bkz. `src/config/settings.py`).
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


def test_signed_in_user_can_start_a_run_with_no_key_of_their_own(multi_user_client, monkeypatch):
    """LLM/YouTube arama anahtari ARTIK PAYLASIMLI: oturum acan HERHANGI bir
    kullanici, hicbir anahtar kaydetmeden, sunucunun `.env` anahtariyla
    calistirma baslatabilmeli.
    """
    from api.routers import runs as runs_router

    monkeypatch.setattr(
        runs_router, "build_playlist", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("dur"))
    )
    multi_user_client.store.create_session("jeton", "google:123", None, ttl_sec=3600)
    multi_user_client.cookies.set("map_session", "jeton")

    response = multi_user_client.post("/api/runs", json={"topic": "Konu"})

    assert response.status_code == 202


def test_server_not_configured_gives_a_clear_503_not_a_silent_failure(tmp_path, monkeypatch):
    """Sunucuda hic LLM anahtari yoksa istek ACIKCA 503 almali.

    Once bu durum 'kullanici anahtarini girmedi' (400) olarak ele aliniyordu;
    artik anahtar hic kullaniciya ait olmadigi icin bu SUNUCU yapilandirma
    hatasi -- 503 daha dogru.
    """
    from fastapi.testclient import TestClient

    from api import deps
    from src.config import AppConfig

    config = AppConfig(
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "app.db"),
        secret_encryption_key=KEY,
        auth_mode="multi_user",
    )
    config.ensure_directories()
    monkeypatch.setattr(deps, "_base_config", lambda: config)

    from api.main import app

    with TestClient(app) as client:
        store = SQLiteStore(config.sqlite_path, encryption_key=KEY)
        store.create_session("jeton", "google:123", None, ttl_sec=3600)
        client.cookies.set("map_session", "jeton")

        response = client.post("/api/runs", json={"topic": "Konu"})

    assert response.status_code == 503


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


# ------------------------------------------------ saglayici sagligi kapsami

def test_one_users_cooldown_does_not_affect_others(store):
    """Regresyon: `provider_health` KURULUM GENELINDE tutuluyordu.

    Birincil anahtar yalnizca `provider` idi. Bir kullanicinin kota asimi ya da
    art arda hatalari saglayiciyi sogutunca DIGER HERKESIN aramasi da duruyordu.
    Tek kullanicili kurulumda goze batmiyordu; ikinci kullanici gelir gelmez
    somut bir hata.
    """
    store.mark_provider_cooldown("youtube", "kota asildi", 900, user_id="ali")

    assert store.get_provider_cooldown("youtube", user_id="ali") is not None
    assert store.get_provider_cooldown("youtube", user_id="veli") is None


def test_failure_counters_are_counted_per_user(store):
    """Esik sayaci da ayri: birinin hatalari digerini esige yaklastirmamali."""
    for _ in range(2):
        store.record_provider_failure("yt_dlp", "hata", cooldown_sec=900, threshold=3, user_id="ali")

    count, cooled = store.record_provider_failure(
        "yt_dlp", "hata", cooldown_sec=900, threshold=3, user_id="veli"
    )

    assert (count, cooled) == (1, False), "veli, ali'nin sayacini devralmamali"


def test_clearing_one_users_cooldown_leaves_the_other(store):
    store.mark_provider_cooldown("youtube", "hata", 900, user_id="ali")
    store.mark_provider_cooldown("youtube", "hata", 900, user_id="veli")

    store.clear_provider_cooldown("youtube", user_id="ali")

    assert store.get_provider_cooldown("youtube", user_id="ali") is None
    assert store.get_provider_cooldown("youtube", user_id="veli") is not None


def test_existing_rows_survive_the_per_user_migration(tmp_path):
    """Eski veritabani acildiginda kayitlar KAYBOLMAMALI.

    Eski tablo YERINDE DEGISTIRILMIYOR: `DROP` + `RENAME` yapan ilk surum,
    bir baglanti tabloyu dusururken digerinin "no such table" almasina yol
    aciyordu (CI'da kirildi). Yeni tablo ayri adla kuruluyor, satirlar
    kopyalaniyor ve eskisine dokunulmuyor -- yikici adim yok, yaris da yok.
    """
    db_path = tmp_path / "eski.db"
    legacy = sqlite3.connect(db_path)
    legacy.executescript(
        """
        CREATE TABLE provider_health (
            provider TEXT PRIMARY KEY,
            cooldown_until TEXT,
            last_error TEXT,
            updated_at TEXT NOT NULL
        );
        INSERT INTO provider_health VALUES
            ('yt_dlp', '2099-01-01T00:00:00+00:00', 'eski hata', '2026-01-01T00:00:00+00:00');
        """
    )
    legacy.commit()
    legacy.close()

    store = SQLiteStore(str(db_path))

    assert store.get_provider_cooldown("yt_dlp", user_id="local") is not None
    columns = {
        row[1] for row in sqlite3.connect(db_path).execute("PRAGMA table_info(provider_cooldown)")
    }
    assert {"user_id", "failure_count"} <= columns
    # Eski tabloya DOKUNULMAMIS olmali: yikici adim yok.
    legacy = sqlite3.connect(db_path).execute(
        "SELECT COUNT(*) FROM provider_health"
    ).fetchone()[0]
    assert legacy == 1


# ------------------------------------------------------------- hiz siniri

def test_daily_run_limit_is_enforced_per_user(tmp_path, monkeypatch):
    """Sinirsizken tek kullanici gunluk YouTube kotasinin tamamini tuketebilir.

    Kota proje basina 10.000 birim, bir calistirma 612 birim -- yani ~16
    calistirma TUM KULLANICILAR icin toplam.
    """
    from fastapi.testclient import TestClient

    from api import deps
    from api.routers import runs as runs_router
    from src.config import AppConfig

    config = AppConfig(
        gemini_api_key="k",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "app.db"),
        secret_encryption_key=KEY,
        auth_mode="multi_user",
        max_runs_per_user_per_day=2,
    )
    config.ensure_directories()
    monkeypatch.setattr(deps, "_base_config", lambda: config)
    monkeypatch.setattr(runs_router, "build_playlist", lambda *a, **k: None)

    from api.main import app

    with TestClient(app) as client:
        store = SQLiteStore(config.sqlite_path, encryption_key=KEY)
        store.create_session("ali-jeton", "google:ali", None, ttl_sec=3600)
        store.create_session("veli-jeton", "google:veli", None, ttl_sec=3600)

        client.cookies.set("map_session", "ali-jeton")
        assert client.post("/api/runs", json={"topic": "bir"}).status_code == 202
        assert client.post("/api/runs", json={"topic": "iki"}).status_code == 202

        third = client.post("/api/runs", json={"topic": "uc"})
        assert third.status_code == 429
        assert third.headers.get("Retry-After")

        # Sinir KULLANICI BASINA: veli ali'nin harcamasindan etkilenmemeli.
        client.cookies.set("map_session", "veli-jeton")
        assert client.post("/api/runs", json={"topic": "veli"}).status_code == 202


def test_zero_means_unlimited(store):
    """Varsayilan 0: tek kullanicili kurulumda sinir koymak anlamsiz."""
    from src.config import ServerConfig

    assert ServerConfig().max_runs_per_user_per_day == 0


# ------------------------------------------------- kesilen calistirmalar

def test_interrupted_run_is_not_reported_as_pending(tmp_path, monkeypatch):
    """Regresyon: yeniden baslatmada kesilen calistirma SONSUZA KADAR
    "beklemede" gorunuyordu.

    Surec yeniden baslatildiginda bellekteki isler oluyor ama `run` satiri
    kaliyor. Sonucu olmayan ve canli isi de olmayan bir calistirmayi
    "beklemede" gostermek yalan: onu bitirecek hicbir sey kalmadi.

    Kaldigi yerden SURDURMEK kalici bir kuyruk ister (Faz 3); buradaki is
    yalan soylememek.
    """
    from fastapi.testclient import TestClient

    from api import deps
    from src.config import AppConfig

    config = AppConfig(
        gemini_api_key="k",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "app.db"),
        secret_encryption_key=KEY,
        auth_mode="multi_user",
    )
    config.ensure_directories()
    monkeypatch.setattr(deps, "_base_config", lambda: config)

    from api.main import app

    with TestClient(app) as client:
        store = SQLiteStore(config.sqlite_path, encryption_key=KEY)
        # Yeniden baslatmadan sonraki hal: satir var, sonuc yok, bellekte is yok.
        store.create_run("yarim-kalan", "Konu", {}, user_id="google:ali")
        store.create_session("jeton", "google:ali", None, ttl_sec=3600)
        client.cookies.set("map_session", "jeton")

        body = client.get("/api/runs/yarim-kalan").json()

    assert body["state"] == "interrupted"
    assert body["result"] is None
