from src.config import AppConfig
from src.models import FilterOptions, VideoCandidate
from src.storage import SQLiteStore


def test_sqlite_search_cache_hit_and_miss(tmp_path):
    config = AppConfig(
        gemini_api_key="test",
        gemini_model="gemini-test",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
    )
    config.ensure_directories()
    store = SQLiteStore(config.sqlite_path)
    filters = FilterOptions(language="en")
    candidate = VideoCandidate(video_id="vid12345678", url="https://example.com", title="Test")

    store.put_search_cache("yt_dlp", "python", filters.model_dump(), [candidate], ttl_sec=60)
    cached = store.get_search_cache("yt_dlp", "python", filters.model_dump())
    assert cached is not None
    assert cached[0].video_id == "vid12345678"

    store.put_search_cache("yt_dlp", "expired", filters.model_dump(), [candidate], ttl_sec=0)
    assert store.get_search_cache("yt_dlp", "expired", filters.model_dump()) is None
