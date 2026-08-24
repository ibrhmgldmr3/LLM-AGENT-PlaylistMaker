"""Kutuphane istisnalarinin DOGRU kovaya dustugunu kilitler.

Siniflandirma boslugu sessiz bir bozulma bicimi: siniflandirilmamis bir
istisna genel `except Exception` dalina duser, saglayicinin ardisik hata
sayacini artirir ve ucuncu videoda sunucu geneli cooldown'a yol acar. Yani
"bu videoda altyazi yok" ile "kurulumun eksik" ayni sonuca varir.
"""

from __future__ import annotations

import pytest
import youtube_transcript_api as yta

from src.config import AppConfig
from src.models import TranscriptResult, VideoCandidate
from src.providers import youtube_transcript_api_provider as provider_module
from src.providers.errors import (
    ProviderPermanentError,
    ProviderRateLimitedError,
    ProviderTemporaryError,
    VideoUnavailableError,
)
from src.providers.youtube_transcript_api_provider import YouTubeTranscriptAPIProvider
from src.services import transcript_service
from src.storage import SQLiteStore
from src.utils.http_identity import reset_identity_cache


VIDEO_ID = "abc123def45"


@pytest.fixture(autouse=True)
def _clear_identity_cache():
    reset_identity_cache()
    yield
    reset_identity_cache()


def _config(tmp_path, **overrides):
    values = dict(
        gemini_api_key="test",
        gemini_model="gemini-test",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
        retry_max_attempts=1,
        retry_base_delay_sec=0.01,
    )
    values.update(overrides)
    config = AppConfig(**values)
    config.ensure_directories()
    return config


def _raise_from_fetch(monkeypatch, exc: Exception) -> None:
    def boom(self, video_id, languages):
        raise exc

    monkeypatch.setattr(YouTubeTranscriptAPIProvider, "_fetch_raw", boom)


@pytest.mark.parametrize(
    "library_error, expected",
    [
        # Videoya ozgu: saglayici saglikli.
        (yta.InvalidVideoId(VIDEO_ID), VideoUnavailableError),
        (yta.TranscriptsDisabled(VIDEO_ID), VideoUnavailableError),
        (yta.AgeRestricted(VIDEO_ID), VideoUnavailableError),
        # Saglayici duzeyinde, gecici.
        (yta.YouTubeDataUnparsable(VIDEO_ID), ProviderTemporaryError),
        (yta.YouTubeRequestFailed(VIDEO_ID, Exception("boom")), ProviderTemporaryError),
        # Hiz siniri: tekrar denemek durumu kotulestirir.
        (yta.RequestBlocked(VIDEO_ID), ProviderRateLimitedError),
        (yta.IpBlocked(VIDEO_ID), ProviderRateLimitedError),
        # Yapilandirma: ne tekrar deneme ne dinlendirme duzeltir.
        (yta.PoTokenRequired(VIDEO_ID), ProviderPermanentError),
    ],
)
def test_library_errors_map_to_expected_category(tmp_path, monkeypatch, library_error, expected):
    _raise_from_fetch(monkeypatch, library_error)
    with pytest.raises(expected):
        YouTubeTranscriptAPIProvider(_config(tmp_path)).fetch(VIDEO_ID, "en")


def test_invalid_video_id_is_not_a_provider_fault(tmp_path, monkeypatch):
    """Bozuk bir video kimligi saglayicinin sagligi hakkinda hicbir sey soylemez."""
    _raise_from_fetch(monkeypatch, yta.InvalidVideoId(VIDEO_ID))
    with pytest.raises(VideoUnavailableError):
        YouTubeTranscriptAPIProvider(_config(tmp_path)).fetch(VIDEO_ID, "en")


def test_po_token_error_tells_the_operator_what_to_do(tmp_path, monkeypatch):
    _raise_from_fetch(monkeypatch, yta.PoTokenRequired(VIDEO_ID))
    with pytest.raises(ProviderPermanentError) as caught:
        YouTubeTranscriptAPIProvider(_config(tmp_path)).fetch(VIDEO_ID, "en")

    message = str(caught.value)
    assert "YTDLP_COOKIES_FILE" in message
    assert "YTDLP_PROXY" in message


def test_unclassified_library_error_becomes_temporary_not_generic(tmp_path, monkeypatch):
    """Kutuphane yarin yeni bir istisna eklerse sessizce cezalandirmamali."""

    class FutureLibraryError(yta.CouldNotRetrieveTranscript):
        pass

    _raise_from_fetch(monkeypatch, FutureLibraryError(VIDEO_ID))
    with pytest.raises(ProviderTemporaryError):
        YouTubeTranscriptAPIProvider(_config(tmp_path)).fetch(VIDEO_ID, "en")


def test_missing_exception_name_falls_back_to_never_raised(tmp_path):
    """`_exc` yer tutucusu gercek hatalari yakalamamali.

    Eskiden yer tutucu `LookupError` idi: aranan ad kutuphanede yoksa dal
    `KeyError`/`IndexError` yakalamaya baslardi, yani gercek bir kodlama
    hatasi "altyazi bulunamadi" gibi gorunurdu.
    """
    fallback = provider_module._exc("BoyleBirIstisnaYok")
    assert fallback == (provider_module._NeverRaised,)
    assert not issubclass(provider_module._NeverRaised, LookupError)


# --------------------------------------------------------------- servis katmani


def test_config_error_stops_asking_the_provider_for_every_video(tmp_path, monkeypatch):
    """Kurulum hatasi TEK videoyla degil kurulumla ilgili.

    Eskiden 12 adayin 12'sinde de deneniyor, her seferinde ayni sekilde
    basarisiz oluyordu.
    """
    config = _config(tmp_path)
    store = SQLiteStore(config.sqlite_path)
    state = transcript_service.RunTranscriptState()

    calls = {"n": 0}

    def po_token_failure(*args, **kwargs):
        calls["n"] += 1
        raise ProviderPermanentError("PO token gerekiyor")

    def no_subtitles(*args, **kwargs):
        return TranscriptResult(video_id="x", status="unavailable", source="yt_dlp_subtitles")

    monkeypatch.setattr(transcript_service, "_fetch_youtube_transcript", po_token_failure)
    monkeypatch.setattr(transcript_service, "_fetch_ytdlp_subtitles", no_subtitles)

    for index in range(3):
        candidate = VideoCandidate(
            video_id=f"video{index}xxxxx",
            url=f"https://www.youtube.com/watch?v=video{index}xxxxx",
            title="t",
        )
        transcript_service.get_transcript(config, store, candidate, str(tmp_path), state)

    assert calls["n"] == 1, "kurulum hatasindan sonra saglayici tekrar sorgulanmamali"
    assert store.get_provider_cooldown("youtube_transcript_api") is not None


def test_config_error_is_not_counted_as_a_rate_limit(tmp_path, monkeypatch):
    """"Bugun kac kez hiz sinirina takildik" sayaci kirlenmemeli."""
    config = _config(tmp_path)
    store = SQLiteStore(config.sqlite_path)
    state = transcript_service.RunTranscriptState()

    monkeypatch.setattr(
        transcript_service,
        "_fetch_youtube_transcript",
        lambda *a, **k: (_ for _ in ()).throw(ProviderPermanentError("PO token gerekiyor")),
    )
    monkeypatch.setattr(
        transcript_service,
        "_fetch_ytdlp_subtitles",
        lambda *a, **k: TranscriptResult(video_id="x", status="unavailable", source="yt_dlp_subtitles"),
    )

    candidate = VideoCandidate(
        video_id=VIDEO_ID, url=f"https://www.youtube.com/watch?v={VIDEO_ID}", title="t"
    )
    transcript_service.get_transcript(config, store, candidate, str(tmp_path), state)

    events = {(row["provider"], row["event"]): row["count"] for row in store.get_provider_events()}
    assert events.get(("youtube_transcript_api", "misconfigured")) == 1
    assert ("youtube_transcript_api", "rate_limited") not in events
