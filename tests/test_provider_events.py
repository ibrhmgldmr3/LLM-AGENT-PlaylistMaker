"""Gunluk saglayici olay sayaclari (`provider_event`).

Bu sayaclar bir raporlama susu degil, ES ZAMANLILIK AYARININ olcum araci:
"kac calistirma x kac isci" sorusu tahminle degil, gun icinde kac kez hiz
sinirina takildigimiza bakilarak yanitlanmali (bkz. `GET /api/admin/usage`).

Bir olcum aracinin en kotu arizasi sessizce hicbir sey kaydetmemesidir:
tablo bos kalir, bos tablo da "sorun yok" gibi okunur. Buradaki testler
sayacin gercekten arttigini kilitliyor -- sonuncusu, uydurma bir store
cagrisiyla degil, servis yolundan gecen gercek bir 429 ile.
"""

from src.config import AppConfig
from src.models import FilterOptions, VideoCandidate
from src.providers.errors import ProviderRateLimitedError
from src.services import youtube_search_service as search_service
from src.storage import SQLiteStore
from src.storage.sqlite_store import quota_day


def _store(tmp_path) -> SQLiteStore:
    return SQLiteStore(str(tmp_path / "cache" / "app.db"))


def _counts(store: SQLiteStore) -> dict[tuple[str, str], int]:
    return {(row["provider"], row["event"]): row["count"] for row in store.get_provider_events()}


def test_rate_limit_increments_the_daily_counter(tmp_path):
    store = _store(tmp_path)

    store.mark_provider_cooldown("youtube_data_api", "429", 1800)
    store.mark_provider_cooldown("youtube_data_api", "429", 1800)

    assert _counts(store)[("youtube_data_api", "rate_limited")] == 2


def test_failure_below_threshold_counts_as_failure_only(tmp_path):
    """Esigin altindaki hata sayilmali ama soguma olarak sayilMAmali.

    Ikisini ayirmak, "cok hata aliyoruz" ile "saglayici kapandi" durumlarini
    ayirt edebilmek icin: birincisi gurultu, ikincisi calistirmayi durduruyor.
    """
    store = _store(tmp_path)

    failure_count, cooled_down = store.record_provider_failure("yt_dlp", "bom", 900, threshold=3)

    assert (failure_count, cooled_down) == (1, False)
    counts = _counts(store)
    assert counts[("yt_dlp", "failure")] == 1
    assert ("yt_dlp", "cooldown") not in counts


def test_reaching_the_threshold_counts_both(tmp_path):
    store = _store(tmp_path)

    for _ in range(3):
        store.record_provider_failure("yt_dlp", "bom", 900, threshold=3)

    counts = _counts(store)
    assert counts[("yt_dlp", "failure")] == 3
    assert counts[("yt_dlp", "cooldown")] == 1


def test_counters_are_per_day_and_sorted_by_frequency(tmp_path):
    store = _store(tmp_path)

    store.mark_provider_cooldown("youtube_data_api", "429", 1800)
    store.mark_provider_cooldown("youtube_data_api", "429", 1800)
    store.record_provider_failure("yt_dlp", "bom", 900, threshold=99)

    events = store.get_provider_events()
    assert [(e["provider"], e["event"], e["count"]) for e in events] == [
        ("youtube_data_api", "rate_limited", 2),
        ("yt_dlp", "failure", 1),
    ], "en sik olan basta olmali"

    assert store.get_provider_events(day="1999-01-01") == [], "gun kovasi ayrismali"
    assert store.get_provider_events(day=quota_day()) == events


def test_real_429_through_the_search_path_is_counted(tmp_path, monkeypatch):
    """Sayac servis yoluna BAGLI mi.

    Store'u dogrudan cagiran testler, cagrinin gercek hata yolunda hic
    yapilmamasi durumunu goremez. Bu test 429'u saglayici seviyesinde atip
    sayaci sonundan okuyor.
    """
    config = AppConfig(
        gemini_api_key="test",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
        retry_max_attempts=3,
        retry_base_delay_sec=0.01,
        rate_limit_cooldown_sec=1800,
        youtube_data_api_key="fake",
    )
    config.ensure_directories()
    store = SQLiteStore(config.sqlite_path)

    class _RateLimited:
        name = "youtube_data_api"

        def is_available(self) -> bool:
            return True

        def search(self, *args, **kwargs):
            raise ProviderRateLimitedError("429")

    class _Ok:
        name = "yt_dlp"

        def is_available(self) -> bool:
            return True

        def search(self, *args, **kwargs):
            return [VideoCandidate(video_id="v1", url="https://youtu.be/v1", title="T", language="en")]

    monkeypatch.setattr(search_service, "YouTubeDataAPIProvider", lambda c, **kw: _RateLimited())
    monkeypatch.setattr(search_service, "YtDlpProvider", lambda c, **kw: _Ok())

    search_service.search_candidates(config, store, "q", FilterOptions())

    assert _counts(store).get(("youtube_data_api", "rate_limited")) == 1
