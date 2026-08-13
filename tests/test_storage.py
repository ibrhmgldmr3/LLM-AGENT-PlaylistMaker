from src.config import AppConfig
from src.models import FilterOptions, TranscriptResult, VideoCandidate
from src.storage import SQLiteStore


def _config(tmp_path):
    config = AppConfig(
        gemini_api_key="test",
        gemini_model="gemini-test",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
    )
    config.ensure_directories()
    return config


def test_sqlite_search_cache_hit_and_miss(tmp_path):
    config = _config(tmp_path)
    store = SQLiteStore(config.sqlite_path)
    filters = FilterOptions(language="en")
    candidate = VideoCandidate(video_id="vid12345678", url="https://example.com", title="Test")

    store.put_search_cache("yt_dlp", "python", filters.model_dump(), [candidate], ttl_sec=60)
    cached = store.get_search_cache("yt_dlp", "python", filters.model_dump())
    assert cached is not None
    assert cached[0].video_id == "vid12345678"

    store.put_search_cache("yt_dlp", "expired", filters.model_dump(), [candidate], ttl_sec=0)
    assert store.get_search_cache("yt_dlp", "expired", filters.model_dump()) is None


def test_failure_counter_only_cools_down_after_threshold(tmp_path):
    store = SQLiteStore(_config(tmp_path).sqlite_path)

    count, cooled = store.record_provider_failure("prov", "err", cooldown_sec=900, threshold=3)
    assert (count, cooled) == (1, False)
    assert store.get_provider_cooldown("prov") is None

    count, cooled = store.record_provider_failure("prov", "err", cooldown_sec=900, threshold=3)
    assert (count, cooled) == (2, False)

    count, cooled = store.record_provider_failure("prov", "err", cooldown_sec=900, threshold=3)
    assert (count, cooled) == (3, True)
    assert store.get_provider_cooldown("prov") is not None


def test_success_resets_the_failure_counter(tmp_path):
    store = SQLiteStore(_config(tmp_path).sqlite_path)

    store.record_provider_failure("prov", "err", cooldown_sec=900, threshold=3)
    store.record_provider_failure("prov", "err", cooldown_sec=900, threshold=3)
    store.clear_provider_cooldown("prov")

    count, cooled = store.record_provider_failure("prov", "err", cooldown_sec=900, threshold=3)
    assert (count, cooled) == (1, False)


def test_purge_expired_removes_only_stale_rows(tmp_path):
    store = SQLiteStore(_config(tmp_path).sqlite_path)
    filters = FilterOptions(language="en")
    candidate = VideoCandidate(video_id="keepme12345", url="https://example.com", title="Keep")

    store.put_search_cache("p", "fresh", filters.model_dump(), [candidate], ttl_sec=600)
    store.put_search_cache("p", "stale", filters.model_dump(), [candidate], ttl_sec=0)
    store.put_transcript_cache(
        TranscriptResult(video_id="keepme12345", status="available", source="p", text="x" * 60), ttl_sec=600
    )

    removed = store.purge_expired()

    assert removed >= 1
    assert store.get_search_cache("p", "fresh", filters.model_dump()) is not None
    assert store.get_transcript_cache("keepme12345", "p") is not None


def test_expiry_comparison_is_not_lexicographic(tmp_path):
    """Regresyon: mikrosaniyeli/mikrosaniyesiz ISO karisimi sozluksel karsilastirmayi bozuyordu."""
    store = SQLiteStore(_config(tmp_path).sqlite_path)
    filters = FilterOptions(language="en")
    candidate = VideoCandidate(video_id="abcdefghijk", url="https://example.com", title="T")

    store.put_search_cache("p", "q", filters.model_dump(), [candidate], ttl_sec=3600)
    assert store.get_search_cache("p", "q", filters.model_dump()) is not None


def test_cache_key_changes_with_schema_version(monkeypatch, tmp_path):
    """Model alanlari degistiginde eski onbellek kayitlari servis edilmemeli."""
    from src.storage import sqlite_store

    filters = FilterOptions(language="en").model_dump()
    old_key = SQLiteStore.build_search_cache_key("p", "q", filters)
    monkeypatch.setattr(sqlite_store, "CACHE_SCHEMA_VERSION", 999)
    assert SQLiteStore.build_search_cache_key("p", "q", filters) != old_key


def test_stale_schema_entries_are_treated_as_a_miss(monkeypatch, tmp_path):
    from src.storage import sqlite_store

    store = SQLiteStore(_config(tmp_path).sqlite_path)
    filters = FilterOptions(language="en").model_dump()
    store.put_search_cache("p", "q", filters, [VideoCandidate(video_id="v1234567890", url="u", title="T")], 600)
    assert store.get_search_cache("p", "q", filters) is not None

    monkeypatch.setattr(sqlite_store, "CACHE_SCHEMA_VERSION", 999)
    assert store.get_search_cache("p", "q", filters) is None


def test_schema_has_no_orphan_metadata_table(tmp_path):
    """`video_metadata_cache` uretimde yalnizca YAZILIYOR, hic okunmuyordu.

    Ayni veriyi arama onbellegi zaten tasiyor; katman kaldirildi.
    """
    store = SQLiteStore(_config(tmp_path).sqlite_path)
    with store.connect() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "video_metadata_cache" not in tables
    assert {"search_cache", "transcript_cache", "provider_health", "run"} <= tables
