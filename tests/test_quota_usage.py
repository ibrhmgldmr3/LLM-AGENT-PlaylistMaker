"""Kota olcumu: gercek API cagrilari sayiliyor mu, onbellek isabeti bedava mi.

Bu dosyanin varlik sebebi tek bir soru: "gunluk YouTube kotamdan ne kadar
kaldi?" Yanlis yanit vermektense hic yanit vermemek daha iyi olurdu, o yuzden
olcumun HANGI durumlarda artmadigi da burada test ediliyor.
"""

from __future__ import annotations

import requests

from src.config import AppConfig
from src.models import FilterOptions
from src.providers import youtube_data_api_provider as ytapi
from src.providers.youtube_data_api_provider import YouTubeDataAPIProvider
from src.services import youtube_search_service as search_service
from src.storage import SQLiteStore
from src.storage.sqlite_store import quota_day


def _config(tmp_path, **overrides):
    values = dict(
        gemini_api_key="test",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
        retry_max_attempts=1,
        retry_base_delay_sec=0.01,
        youtube_data_api_key="fake-key",
    )
    values.update(overrides)
    config = AppConfig(**values)
    config.ensure_directories()
    return config


class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _fake_youtube(monkeypatch, *, video_count=2):
    """Uc ucu da taklit eder: search.list -> videos.list -> channels.list."""
    istekler: list[str] = []

    videos = [f"v{i}" for i in range(video_count)]

    def fake_get(url, params=None, timeout=None):
        istekler.append(url)
        if url == ytapi.SEARCH_URL:
            return _Response({"items": [{"id": {"videoId": v}, "snippet": {}} for v in videos]})
        if url == ytapi.VIDEOS_URL:
            return _Response({
                "items": [
                    {
                        "id": v,
                        "snippet": {"title": v, "description": "", "channelId": "c1",
                                    "channelTitle": "Kanal", "publishedAt": "2026-01-01T00:00:00Z"},
                        "contentDetails": {"duration": "PT10M"},
                        "statistics": {"viewCount": "1000"},
                    }
                    for v in videos
                ]
            })
        if url == ytapi.CHANNELS_URL:
            return _Response({"items": [{"id": "c1", "statistics": {"subscriberCount": "5000"}}]})
        raise AssertionError(f"beklenmeyen URL: {url}")

    class _Session:
        get = staticmethod(fake_get)

    monkeypatch.setattr(ytapi, "_session", lambda: _Session())
    return istekler


def test_a_real_search_records_the_units_it_actually_spent(tmp_path, monkeypatch):
    """search.list 100, videos.list 1, channels.list 1 = 102 birim."""
    istekler = _fake_youtube(monkeypatch)
    config = _config(tmp_path)
    store = SQLiteStore(config.sqlite_path, encryption_key=None)

    search_service.search_candidates(
        config, store, "kuantum", FilterOptions(), user_id="ali"
    )

    assert istekler == [ytapi.SEARCH_URL, ytapi.VIDEOS_URL, ytapi.CHANNELS_URL]

    kullanim = {row["endpoint"]: row for row in store.get_api_usage()}
    assert kullanim["search.list"]["units"] == 100
    assert kullanim["videos.list"]["units"] == 1
    assert kullanim["channels.list"]["units"] == 1
    assert sum(row["units"] for row in store.get_api_usage()) == 102
    assert all(row["user_id"] == "ali" for row in store.get_api_usage())


def test_a_cache_hit_spends_nothing(tmp_path, monkeypatch):
    """Onbellekten donen arama SIFIR kota harcamali.

    Tam da bu yuzden tuketim "calistirma sayisi x 1.200" diye TAHMIN
    edilemiyor: ayni konu 6 saat icinde tekrar islenirse hic kota gitmiyor.
    """
    istekler = _fake_youtube(monkeypatch)
    config = _config(tmp_path)
    store = SQLiteStore(config.sqlite_path, encryption_key=None)

    search_service.search_candidates(config, store, "kuantum", FilterOptions(), user_id="ali")
    ilk_tur = sum(row["units"] for row in store.get_api_usage())
    cagri_sayisi = len(istekler)

    # Ayni sorgu yeniden: onbellekten donmeli.
    search_service.search_candidates(config, store, "kuantum", FilterOptions(), user_id="ali")

    assert len(istekler) == cagri_sayisi, "onbellek isabetinde API'ye gidilmemeliydi"
    assert sum(row["units"] for row in store.get_api_usage()) == ilk_tur


def test_usage_is_attributed_per_user(tmp_path, monkeypatch):
    _fake_youtube(monkeypatch)
    config = _config(tmp_path)
    store = SQLiteStore(config.sqlite_path, encryption_key=None)

    search_service.search_candidates(config, store, "konu-a", FilterOptions(), user_id="ali")
    search_service.search_candidates(config, store, "konu-b", FilterOptions(), user_id="veli")

    per_user: dict[str, int] = {}
    for row in store.get_api_usage():
        per_user[row["user_id"]] = per_user.get(row["user_id"], 0) + row["units"]

    assert per_user == {"ali": 102, "veli": 102}


def test_failed_calls_are_not_counted(tmp_path):
    """Kotasi dolmus istek Google tarafindan ucretlendirilmiyor; biz de saymayiz."""
    config = _config(tmp_path)
    kayitlar: list[tuple[str, int]] = []

    provider = YouTubeDataAPIProvider(
        config, usage_recorder=lambda endpoint, units: kayitlar.append((endpoint, units))
    )

    class _Failing:
        @staticmethod
        def get(url, params=None, timeout=None):
            raise requests.RequestException("baglanti koptu")

    import src.providers.youtube_data_api_provider as module

    original = module._session
    module._session = lambda: _Failing()
    try:
        try:
            provider.search("kuantum", FilterOptions(), limit=5)
        except Exception:
            pass
    finally:
        module._session = original

    assert kayitlar == []


def test_a_broken_recorder_does_not_break_the_search(tmp_path, monkeypatch):
    """Olcum, islevin kendisinden daha az onemli."""
    _fake_youtube(monkeypatch)
    config = _config(tmp_path)

    def patlayan(endpoint, units):
        raise RuntimeError("sayac yazilamadi")

    provider = YouTubeDataAPIProvider(config, usage_recorder=patlayan)
    sonuc = provider.search("kuantum", FilterOptions(), limit=5)

    assert len(sonuc) == 2, "sayac patlasa bile arama sonuc dondurmeli"


def test_quota_day_follows_pacific_time_not_utc():
    """Kota Pasifik saatiyle sifirlaniyor; gun sinirini UTC'ye gore almak kayma yaratir."""
    from datetime import datetime, timezone

    # UTC'ye gore 20 Agustos'un basi; Pasifik'te hala 19 Agustos aksami.
    an = datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc)

    assert quota_day(an) == "2026-08-19"
    assert an.date().isoformat() == "2026-08-20", "test kendi varsayimini dogrulasin"
