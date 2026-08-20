from src.config import AppConfig
from src.models import FilterOptions, VideoCandidate
from src.providers.errors import ProviderPermanentError, ProviderTemporaryError
from src.services import youtube_search_service as search_service
from src.storage import SQLiteStore


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


class FakeProvider:
    def __init__(self, name, result=None, error=None, configured=True):
        self.name = name
        self._result = result if result is not None else []
        self._error = error
        self._configured = configured
        self.calls = 0

    def is_configured(self):
        return self._configured

    def search(self, query, filters, limit):
        self.calls += 1
        if self._error:
            raise self._error
        return self._result


def _install(monkeypatch, primary, fallback):
    monkeypatch.setattr(search_service, "YouTubeDataAPIProvider", lambda config, **kwargs: primary)
    monkeypatch.setattr(search_service, "YtDlpProvider", lambda config, **kwargs: fallback)


def _candidate(video_id="v1"):
    return VideoCandidate(video_id=video_id, url=f"https://youtu.be/{video_id}", title="found")


def test_empty_primary_result_falls_back_to_secondary(tmp_path, monkeypatch):
    """Regresyon: 0 sonuc 'basari' sayilip yt-dlp yedegi hic denenmiyordu."""
    config = _config(tmp_path)
    store = SQLiteStore(config.sqlite_path)
    primary = FakeProvider("youtube_data_api", result=[])
    fallback = FakeProvider("yt_dlp", result=[_candidate()])
    _install(monkeypatch, primary, fallback)

    result = search_service.search_candidates(config, store, "q", FilterOptions())

    assert [item.video_id for item in result] == ["v1"]
    assert fallback.calls == 1


def test_empty_result_is_not_cached_for_the_long_ttl(tmp_path, monkeypatch):
    """Regresyon: bos sonuc 6 saat onbelleklenip yedegi kalici olarak bloke ediyordu."""
    config = _config(tmp_path, search_cache_ttl_sec=21600, failure_cache_ttl_sec=0)
    store = SQLiteStore(config.sqlite_path)
    primary = FakeProvider("youtube_data_api", result=[])
    fallback = FakeProvider("yt_dlp", result=[_candidate()])
    _install(monkeypatch, primary, fallback)

    search_service.search_candidates(config, store, "q", FilterOptions())

    # failure_cache_ttl_sec=0 oldugu icin bos kayit aninda gecersiz olmali.
    assert store.get_search_cache("youtube_data_api", "q", FilterOptions().model_dump()) is None


def test_cached_empty_result_still_allows_fallback(tmp_path, monkeypatch):
    config = _config(tmp_path)
    store = SQLiteStore(config.sqlite_path)
    store.put_search_cache("youtube_data_api", "q", FilterOptions().model_dump(), [], ttl_sec=600)
    primary = FakeProvider("youtube_data_api", result=[])
    fallback = FakeProvider("yt_dlp", result=[_candidate("v2")])
    _install(monkeypatch, primary, fallback)

    result = search_service.search_candidates(config, store, "q", FilterOptions())

    assert [item.video_id for item in result] == ["v2"]
    assert primary.calls == 0  # onbellek sayesinde tekrar cagrilmadi


def test_single_failure_does_not_trigger_cooldown(tmp_path, monkeypatch):
    """Regresyon: tek bir hata saglayiciyi 15 dakika devre disi birakiyordu."""
    config = _config(tmp_path, provider_failure_threshold=3)
    store = SQLiteStore(config.sqlite_path)
    primary = FakeProvider("youtube_data_api", error=ProviderTemporaryError("boom"))
    fallback = FakeProvider("yt_dlp", result=[_candidate()])
    _install(monkeypatch, primary, fallback)

    search_service.search_candidates(config, store, "q", FilterOptions())

    assert store.get_provider_cooldown("youtube_data_api") is None


def test_cooldown_applies_after_threshold_is_reached(tmp_path, monkeypatch):
    config = _config(tmp_path, provider_failure_threshold=3)
    store = SQLiteStore(config.sqlite_path)
    primary = FakeProvider("youtube_data_api", error=ProviderTemporaryError("boom"))
    fallback = FakeProvider("yt_dlp", result=[_candidate()])
    _install(monkeypatch, primary, fallback)

    for index in range(3):
        search_service.search_candidates(config, store, f"q{index}", FilterOptions())

    assert store.get_provider_cooldown("youtube_data_api") is not None


def test_permanent_error_is_not_retried_and_not_penalised(tmp_path, monkeypatch):
    config = _config(tmp_path, retry_max_attempts=3)
    store = SQLiteStore(config.sqlite_path)
    primary = FakeProvider("youtube_data_api", error=ProviderPermanentError("invalid key"))
    fallback = FakeProvider("yt_dlp", result=[_candidate()])
    _install(monkeypatch, primary, fallback)

    result = search_service.search_candidates(config, store, "q", FilterOptions())

    assert primary.calls == 1  # tekrar denenmedi
    assert store.get_provider_cooldown("youtube_data_api") is None
    assert [item.video_id for item in result] == ["v1"]


def test_api_key_is_redacted_from_notes(tmp_path, monkeypatch):
    config = _config(tmp_path)
    store = SQLiteStore(config.sqlite_path)
    leaked = "https://www.googleapis.com/youtube/v3/search?q=x&key=AIzaSyTOPSECRETVALUE1234"
    primary = FakeProvider("youtube_data_api", error=ProviderTemporaryError(f"failed: {leaked}"))
    fallback = FakeProvider("yt_dlp", result=[], configured=True)
    _install(monkeypatch, primary, fallback)

    notes: list[str] = []
    search_service.search_candidates(config, store, "q", FilterOptions(), notes=notes)

    joined = " ".join(notes)
    assert "AIzaSyTOPSECRETVALUE1234" not in joined
    assert "***" in joined
