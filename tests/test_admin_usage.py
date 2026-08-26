"""Kullanim raporu ucu ve kullanicinin kendi kalan hakki.

Bu ucun gosterdigi sey sir degil ama masum da degil: kullanici kimlikleri ve
kimin ne kadar harcadigi. Yetki testleri bu yuzden burada agirlikta.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.config import settings
from src.config import AppConfig
from src.storage import SQLiteStore
from src.storage.sqlite_store import quota_day

KEY = "k" * 44


def _client(monkeypatch, tmp_path, **overrides):

    config = AppConfig(
        gemini_api_key="k",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "app.db"),
        secret_encryption_key=KEY,
        **overrides,
    )
    config.ensure_directories()
    monkeypatch.setattr(settings, "base_config", lambda: config)

    from api.main import app

    return TestClient(app), SQLiteStore(config.sqlite_path, encryption_key=KEY)


# ------------------------------------------------------------------- yetki

def test_single_user_mode_always_sees_the_report(monkeypatch, tmp_path):
    """Tek kullanicili kurulumda ayri bir yetki kavrami uydurmak anlamsiz."""
    client, _ = _client(monkeypatch, tmp_path, auth_mode="single_user")
    with client:
        assert client.get("/api/admin/usage").status_code == 200


def test_multi_user_without_an_admin_list_denies_everyone(monkeypatch, tmp_path):
    """BOS liste = HERKESE KAPALI.

    Ters varsayilan (liste bossa herkese acik), `ADMIN_USER_IDS` tanimlamayi
    unutan her kurulumda kullanici kimliklerini ve kullanim aliskanligini
    giris yapmis herkese gosterirdi -- sessizce olusan turden bir sizinti.
    """
    client, store = _client(monkeypatch, tmp_path, auth_mode="multi_user")
    with client:
        store.create_session("ali-jeton", "google:ali", None, ttl_sec=3600)
        client.cookies.set("map_session", "ali-jeton")

        assert client.get("/api/admin/usage").status_code == 403


def test_only_listed_admins_see_the_report(monkeypatch, tmp_path):
    client, store = _client(
        monkeypatch, tmp_path, auth_mode="multi_user", admin_user_ids="google:ali"
    )
    with client:
        store.create_session("ali-jeton", "google:ali", None, ttl_sec=3600)
        store.create_session("veli-jeton", "google:veli", None, ttl_sec=3600)

        client.cookies.set("map_session", "ali-jeton")
        assert client.get("/api/admin/usage").status_code == 200

        client.cookies.set("map_session", "veli-jeton")
        assert client.get("/api/admin/usage").status_code == 403


def test_report_needs_a_session_in_multi_user_mode(monkeypatch, tmp_path):
    client, _ = _client(
        monkeypatch, tmp_path, auth_mode="multi_user", admin_user_ids="google:ali"
    )
    with client:
        assert client.get("/api/admin/usage").status_code == 401


# ------------------------------------------------------------------ icerik

def test_report_shows_real_units_and_what_is_left(monkeypatch, tmp_path):
    client, store = _client(monkeypatch, tmp_path, auth_mode="single_user")
    with client:
        store.record_api_usage("local", "youtube_data_api", "search.list", 100)
        store.record_api_usage("local", "youtube_data_api", "search.list", 100)
        store.record_api_usage("local", "youtube_data_api", "videos.list", 1)

        body = client.get("/api/admin/usage").json()

    assert body["quota_day"] == quota_day()
    assert body["quota"]["spent_units"] == 201
    assert body["quota"]["limit_units"] == 10_000
    assert body["quota"]["remaining_units"] == 9_799
    assert body["quota"]["remaining_searches"] == 97  # 9799 // 100
    assert body["by_endpoint"]["search.list"] == {"calls": 2, "units": 200}
    assert body["by_user_units"] == {"local": 201}


def test_raised_quota_is_honoured(monkeypatch, tmp_path):
    """Google Cloud'dan kota arttirildiysa rapor 10.000'de israr etmemeli."""
    client, store = _client(
        monkeypatch, tmp_path, auth_mode="single_user", youtube_daily_quota_units=1_000_000
    )
    with client:
        store.record_api_usage("local", "youtube_data_api", "search.list", 100)
        body = client.get("/api/admin/usage").json()

    assert body["quota"]["limit_units"] == 1_000_000
    assert body["quota"]["remaining_units"] == 999_900


def test_yesterdays_usage_is_not_counted_today(monkeypatch, tmp_path):
    client, store = _client(monkeypatch, tmp_path, auth_mode="single_user")
    with client:
        store.record_api_usage("local", "youtube_data_api", "search.list", 100, day="2020-01-01")
        body = client.get("/api/admin/usage").json()

    assert body["quota"]["spent_units"] == 0


# ------------------------------------------- kullanicinin kendi kalan hakki

def test_capabilities_reports_remaining_runs(monkeypatch, tmp_path):
    client, store = _client(
        monkeypatch, tmp_path, auth_mode="single_user", max_runs_per_user_per_day=3
    )
    with client:
        assert client.get("/api/config").json()["runs_remaining_today"] == 3

        store.create_run_within_daily_limit("r1", "konu", {}, user_id="local", max_per_day=3)
        assert client.get("/api/config").json()["runs_remaining_today"] == 2


def test_remaining_runs_is_null_when_there_is_no_limit(monkeypatch, tmp_path):
    """Sinir yoksa arayuzde hicbir sey gosterilmemeli -- 0 ile karistirilmasin."""
    client, _ = _client(
        monkeypatch, tmp_path, auth_mode="single_user", max_runs_per_user_per_day=0
    )
    with client:
        assert client.get("/api/config").json()["runs_remaining_today"] is None


def test_remaining_runs_never_goes_negative(monkeypatch, tmp_path):
    client, store = _client(
        monkeypatch, tmp_path, auth_mode="single_user", max_runs_per_user_per_day=1
    )
    with client:
        # Sinir sonradan dusurulmus gibi: gecmiste sinirdan fazla calistirma var.
        for index in range(3):
            store.create_run_within_daily_limit(f"r{index}", "k", {}, user_id="local", max_per_day=0)

        assert client.get("/api/config").json()["runs_remaining_today"] == 0


# --------------------------------- yetenek ucu oturum ZORUNLU KILMAMALI

def test_capabilities_answers_without_a_session_in_multi_user_mode(monkeypatch, tmp_path):
    """`/api/config` oturumsuz istekte de yanit vermeli.

    Tavuk-yumurta: arayuz daha giris ekranini cizerken yetenekleri soruyor.
    Bu uc 401 (ya da 400) donerse istemci hatayi yutuyor ve `capabilities`
    `null` kaliyor -- yani "YouTube anahtari tanimli degil" gibi TUM uyarilar
    sessizce kayboluyor. Ucun isi "ne yapilandirilmis" sorusunu yanitlamak;
    yanitladigi seye bagimli olamaz.

    Bu test bir kez gercekten gerekti: kalan calistirma hakki eklenirken uca
    `get_current_user` bagimliligi girdi ve uc `multi_user` modda 401 donmeye
    basladi. Paketteki hicbir test yakalamadi cunku hepsi `single_user`
    modunda calisiyordu.
    """
    client, _ = _client(
        monkeypatch, tmp_path, auth_mode="multi_user", max_runs_per_user_per_day=3
    )
    with client:
        response = client.get("/api/config")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["llm_configured"] is True
    # Kullaniciya OZGU alan oturumsuzken bos; geri kalan yanit dolu.
    assert body["runs_remaining_today"] is None


def test_capabilities_includes_remaining_runs_once_signed_in(monkeypatch, tmp_path):
    client, store = _client(
        monkeypatch, tmp_path, auth_mode="multi_user", max_runs_per_user_per_day=3
    )
    with client:
        store.create_session("ali-jeton", "google:ali", None, ttl_sec=3600)
        client.cookies.set("map_session", "ali-jeton")

        assert client.get("/api/config").json()["runs_remaining_today"] == 3


# ------------------------------------------- saglayici olaylari (hiz siniri)

def test_report_counts_rate_limit_hits(monkeypatch, tmp_path):
    """"Bu IP bugun kac kez hiz sinirina takildi" -- eskiden cevabi yoktu.

    `provider_cooldown` yalnizca ANLIK durumu tutuyor ve sure dolunca iz
    birakmadan kayboluyor. Es zamanlilik ayarlarini tahminle degil olcumle
    degistirebilmek icin gunun toplami gerekiyor.
    """
    client, store = _client(monkeypatch, tmp_path, auth_mode="single_user")
    with client:
        store.mark_provider_cooldown("yt_dlp", "429", 1800)
        store.mark_provider_cooldown("yt_dlp", "429", 1800)
        store.mark_provider_cooldown("youtube_transcript_api", "429", 1800)

        body = client.get("/api/admin/usage").json()

    olaylar = {(e["provider"], e["event"]): e["count"] for e in body["provider_events"]}
    assert olaylar[("yt_dlp", "rate_limited")] == 2
    assert olaylar[("youtube_transcript_api", "rate_limited")] == 1


def test_report_separates_failures_from_the_cooldown_they_trigger(monkeypatch, tmp_path):
    """Esik dolana kadar hata sayilir; dolunca AYRICA bir soguma olayi yazilir."""
    client, store = _client(monkeypatch, tmp_path, auth_mode="single_user")
    with client:
        for _ in range(3):
            store.record_provider_failure("yt_dlp", "hata", cooldown_sec=900, threshold=3)

        body = client.get("/api/admin/usage").json()

    olaylar = {(e["provider"], e["event"]): e["count"] for e in body["provider_events"]}
    assert olaylar[("yt_dlp", "failure")] == 3
    assert olaylar[("yt_dlp", "cooldown")] == 1
    assert ("yt_dlp", "rate_limited") not in olaylar, "genel hata, hiz siniri degil"


def test_active_cooldowns_show_the_server_scope(monkeypatch, tmp_path):
    client, store = _client(monkeypatch, tmp_path, auth_mode="single_user")
    with client:
        store.mark_provider_cooldown("yt_dlp", "429", 1800)
        body = client.get("/api/admin/usage").json()

    assert len(body["active_cooldowns"]) == 1
    assert body["active_cooldowns"][0]["provider"] == "yt_dlp"
    assert body["active_cooldowns"][0]["scope"] == "__server__"
