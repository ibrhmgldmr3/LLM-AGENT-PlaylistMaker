"""Hiz siniri (HTTP 429 / IP blogu) davranisi.

Regresyon: 429 genel bir "gecici hata" sayiliyordu. Sonuc olarak her video icin
3 kez tekrar deneniyor, 4 paralel worker ve 2 saglayici ile tek bir hiz siniri
olayi ~24 iste ge donusuyor ve YouTube'un IP blogunu derinlestiriyordu.
"""

from src.config import AppConfig
from src.models import FilterOptions, TranscriptResult, VideoCandidate
from src.providers.errors import ProviderRateLimitedError, ProviderTemporaryError
from src.services import transcript_service
from src.services import youtube_search_service as search_service
from src.storage import SQLiteStore


def _config(tmp_path, **overrides):
    values = dict(
        gemini_api_key="test",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
        retry_max_attempts=3,
        retry_base_delay_sec=0.01,
        rate_limit_cooldown_sec=1800,
        provider_failure_threshold=3,
        youtube_data_api_key="fake",
    )
    values.update(overrides)
    config = AppConfig(**values)
    config.ensure_directories()
    return config


def _candidate(video_id: str = "abc123def45"):
    return VideoCandidate(
        video_id=video_id, url=f"https://youtu.be/{video_id}", title="T", language="en"
    )


# ------------------------------------------------------------------ transkript

def test_rate_limit_is_not_retried(tmp_path, monkeypatch):
    config = _config(tmp_path)
    store = SQLiteStore(config.sqlite_path)
    calls = {"n": 0}

    def rate_limited(*args, **kwargs):
        calls["n"] += 1
        raise ProviderRateLimitedError("429 Too Many Requests")

    monkeypatch.setattr(transcript_service, "_fetch_youtube_transcript", rate_limited)
    monkeypatch.setattr(
        transcript_service, "_fetch_ytdlp_subtitles",
        lambda *a, **k: TranscriptResult(video_id="abc123def45", status="unavailable", source="yt_dlp_subtitles"),
    )

    transcript_service.get_transcript(
        config, store, _candidate(), str(tmp_path), transcript_service.RunTranscriptState()
    )

    assert calls["n"] == 1, "hiz sinirinda tekrar denenmemeli"


def test_ordinary_temporary_error_is_still_retried(tmp_path, monkeypatch):
    """Hiz siniri disindaki gecici hatalar eskisi gibi tekrar denenmeli."""
    config = _config(tmp_path, retry_max_attempts=3)
    store = SQLiteStore(config.sqlite_path)
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        raise ProviderTemporaryError("geçici ağ hatası")

    monkeypatch.setattr(transcript_service, "_fetch_youtube_transcript", flaky)
    monkeypatch.setattr(
        transcript_service, "_fetch_ytdlp_subtitles",
        lambda *a, **k: TranscriptResult(video_id="abc123def45", status="unavailable", source="yt_dlp_subtitles"),
    )

    transcript_service.get_transcript(
        config, store, _candidate(), str(tmp_path), transcript_service.RunTranscriptState()
    )

    assert calls["n"] == 3


def test_rate_limit_cools_down_immediately_without_threshold(tmp_path, monkeypatch):
    """Tek bir 429, esik beklenmeden saglayiciyi dinlendirmeli."""
    config = _config(tmp_path, provider_failure_threshold=3)
    store = SQLiteStore(config.sqlite_path)

    monkeypatch.setattr(
        transcript_service, "_fetch_youtube_transcript",
        lambda *a, **k: (_ for _ in ()).throw(ProviderRateLimitedError("429")),
    )
    monkeypatch.setattr(
        transcript_service, "_fetch_ytdlp_subtitles",
        lambda *a, **k: TranscriptResult(video_id="abc123def45", status="unavailable", source="yt_dlp_subtitles"),
    )

    transcript_service.get_transcript(
        config, store, _candidate(), str(tmp_path), transcript_service.RunTranscriptState()
    )

    assert store.get_provider_cooldown("youtube_transcript_api") is not None


def test_retry_after_header_sets_the_cooldown(tmp_path, monkeypatch):
    config = _config(tmp_path, rate_limit_cooldown_sec=1800)
    store = SQLiteStore(config.sqlite_path)
    captured = {}

    original = store.mark_provider_cooldown

    def spy(provider, error, cooldown_sec, **kwargs):
        captured["seconds"] = cooldown_sec
        return original(provider, error, cooldown_sec, **kwargs)

    monkeypatch.setattr(store, "mark_provider_cooldown", spy)
    monkeypatch.setattr(
        transcript_service, "_fetch_youtube_transcript",
        lambda *a, **k: (_ for _ in ()).throw(ProviderRateLimitedError("429", retry_after=42)),
    )
    monkeypatch.setattr(
        transcript_service, "_fetch_ytdlp_subtitles",
        lambda *a, **k: TranscriptResult(video_id="abc123def45", status="unavailable", source="yt_dlp_subtitles"),
    )

    transcript_service.get_transcript(
        config, store, _candidate(), str(tmp_path), transcript_service.RunTranscriptState()
    )

    assert captured["seconds"] == 42


def test_rate_limit_still_falls_through_to_the_next_provider(tmp_path, monkeypatch):
    config = _config(tmp_path)
    store = SQLiteStore(config.sqlite_path)

    monkeypatch.setattr(
        transcript_service, "_fetch_youtube_transcript",
        lambda *a, **k: (_ for _ in ()).throw(ProviderRateLimitedError("429")),
    )
    monkeypatch.setattr(
        transcript_service, "_fetch_ytdlp_subtitles",
        lambda *a, **k: TranscriptResult(
            video_id="abc123def45", status="available", source="yt_dlp_subtitles", text="x" * 80),
    )

    result = transcript_service.get_transcript(
        config, store, _candidate(), str(tmp_path), transcript_service.RunTranscriptState()
    )

    assert result.status == "available"


# ------------------------------------------------------------------ arama

class _RateLimitedProvider:
    name = "youtube_data_api"

    def __init__(self):
        self.calls = 0

    def is_configured(self):
        return True

    def search(self, query, filters, limit):
        self.calls += 1
        raise ProviderRateLimitedError("429 quota")


class _OkProvider:
    name = "yt_dlp"

    def search(self, query, filters, limit):
        return [VideoCandidate(video_id="v1", url="u", title="ok")]


def test_search_rate_limit_is_not_retried_and_cools_down(tmp_path, monkeypatch):
    config = _config(tmp_path, retry_max_attempts=3)
    store = SQLiteStore(config.sqlite_path)
    primary = _RateLimitedProvider()

    monkeypatch.setattr(search_service, "YouTubeDataAPIProvider", lambda c, **kwargs: primary)
    monkeypatch.setattr(search_service, "YtDlpProvider", lambda c, **kwargs: _OkProvider())

    result = search_service.search_candidates(config, store, "q", FilterOptions())

    assert primary.calls == 1, "hiz sinirinda tekrar denenmemeli"
    assert store.get_provider_cooldown("youtube_data_api") is not None
    assert [item.video_id for item in result] == ["v1"], "yedek saglayici devreye girmeli"


# --------------------------------------------- soguma kapsami: SUNUCU GENELI
#
# Bu iki test bir kez ters yonde yazilmisti (soguma "islemi yapan kullaniciya"
# kapsanmali diye). O zamanki gercek hata YAZMA ve OKUMA taraflarinin FARKLI
# kullanici kimligi kullanmasiydi: soguma hic tutmuyor, saglayici her video
# icin yeniden deneniyordu. Kapsam tek ve sunucu geneli olunca o hata sinifi
# yapisal olarak imkansiz -- yanlis gecilecek bir kimlik kalmadi.
#
# Asil olcut degismedi ve burada korunuyor: hiz sinirina takilan bir cagri,
# SONRAKI cagrilarin gordugu bir soguma birakmali.


def test_transcript_rate_limit_registers_a_cooldown_others_can_see(tmp_path, monkeypatch):
    """Hiz siniri sogumasi yazilmali VE baska bir kullanicinin kosusunu da durdurmali.

    Sinir sunucunun IP'sine bagli: ikinci kullanici ayni yerden cikiyor. Kapsam
    kullanici basinayken o kullanici hic korunmuyor, ayni sinira yeniden
    giriyor ve engeli uzatiyordu.
    """
    config = _config(tmp_path)
    store = SQLiteStore(config.sqlite_path)

    cagrilar: list[str] = []

    def rate_limited(*args, **kwargs):
        cagrilar.append("youtube_transcript_api")
        raise ProviderRateLimitedError("429")

    monkeypatch.setattr(transcript_service, "_fetch_youtube_transcript", rate_limited)
    monkeypatch.setattr(
        transcript_service, "_fetch_ytdlp_subtitles",
        lambda *a, **k: (_ for _ in ()).throw(ProviderRateLimitedError("429")),
    )

    transcript_service.get_transcript(
        config, store, _candidate(), str(tmp_path), transcript_service.RunTranscriptState(),
        user_id="google:ali",
    )

    assert store.get_provider_cooldown("youtube_transcript_api") is not None
    ilk_tur = len(cagrilar)

    # BASKA bir kullanici, baska bir video: saglayici atlanmali.
    transcript_service.get_transcript(
        config, store, _candidate("baska-video"), str(tmp_path),
        transcript_service.RunTranscriptState(), user_id="google:veli",
    )

    assert len(cagrilar) == ilk_tur, "soguma ikinci kullaniciyi da durdurmaliydi"


def test_search_rate_limit_registers_a_cooldown_others_can_see(tmp_path, monkeypatch):
    """`search_candidates` tarafinda ayni olcut."""
    config = _config(tmp_path)
    store = SQLiteStore(config.sqlite_path)

    cagrilar: list[str] = []

    def rate_limited_search(*args, **kwargs):
        cagrilar.append("youtube_data_api")
        raise ProviderRateLimitedError("429 quota")

    monkeypatch.setattr(
        "src.providers.youtube_data_api_provider.YouTubeDataAPIProvider.search", rate_limited_search
    )
    monkeypatch.setattr("src.providers.ytdlp_provider.YtDlpProvider.search", lambda *a, **k: [])

    search_service.search_candidates(config, store, "sorgu", FilterOptions(), user_id="google:ali")

    assert store.get_provider_cooldown("youtube_data_api") is not None
    ilk_tur = len(cagrilar)

    search_service.search_candidates(
        config, store, "baska sorgu", FilterOptions(), user_id="google:veli"
    )

    assert len(cagrilar) == ilk_tur, "soguma ikinci kullaniciyi da durdurmaliydi"
