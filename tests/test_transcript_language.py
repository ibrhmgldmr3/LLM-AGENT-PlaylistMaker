"""Dil dususu ve onbellek anahtarindaki dil.

Iki ayri hata ayni koke bagli: dil, saglayicinin ne yapacagini belirleyen bir
GIRDI, ama ne dususte ne de onbellek anahtarinda dikkate aliniyordu.
"""

from __future__ import annotations

import sqlite3

import pytest
import youtube_transcript_api as yta

from src.config import AppConfig
from src.models import TranscriptResult
from src.providers.errors import VideoUnavailableError
from src.providers.youtube_transcript_api_provider import (
    YouTubeTranscriptAPIProvider,
    _select_transcript,
)
from src.storage import SQLiteStore
from src.utils.http_identity import reset_identity_cache


VIDEO_ID = "abc123def45"
LONG_TEXT = "bu metin MIN_TRANSCRIPT_CHARS esigini gecmek icin yeterince uzun tutuluyor"


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
    )
    values.update(overrides)
    config = AppConfig(**values)
    config.ensure_directories()
    return config


# ----------------------------------------------------------------- test ikizleri


class FakeSnippet:
    def __init__(self, text, start=0.0, duration=3.0):
        self.text = text
        self.start = start
        self.duration = duration


class FakeFetched:
    def __init__(self, language_code, text):
        self.language_code = language_code
        self._snippets = [FakeSnippet(text)]

    def __iter__(self):
        return iter(self._snippets)


class FakeTranslationLanguage:
    def __init__(self, language_code):
        self.language_code = language_code


class FakeTranscript:
    def __init__(self, language_code, is_generated=True, translations=(), translate_error=None):
        self.language_code = language_code
        self.is_generated = is_generated
        self.translation_languages = [FakeTranslationLanguage(code) for code in translations]
        self.is_translatable = bool(translations)
        self._translate_error = translate_error

    def translate(self, language_code):
        if self._translate_error:
            raise self._translate_error
        return FakeTranscript(language_code, is_generated=True)

    def fetch(self):
        return FakeFetched(self.language_code, LONG_TEXT)


class FakeTranscriptList:
    """`find_transcript` yalnizca TAM eslesen dilleri dondurur; gercek
    kutuphane gibi bulamayinca `NoTranscriptFound` firlatir."""

    def __init__(self, transcripts):
        self._transcripts = list(transcripts)

    def __iter__(self):
        return iter(self._transcripts)

    def find_transcript(self, language_codes):
        for code in language_codes:
            for transcript in self._transcripts:
                if transcript.language_code == code:
                    return transcript
        raise yta.NoTranscriptFound(VIDEO_ID, list(language_codes), self)


# --------------------------------------------------------------- dil dususu


def test_preferred_language_is_used_without_falling_back():
    chosen = _select_transcript(
        FakeTranscriptList([FakeTranscript("de"), FakeTranscript("en")]), ["en", "tr"]
    )
    assert chosen.language_code == "en"


def test_only_foreign_captions_are_used_instead_of_giving_up():
    """Yalnizca Almanca altyazisi olan video "altyazisiz" sayilmamali."""
    chosen = _select_transcript(FakeTranscriptList([FakeTranscript("de")]), ["tr", "en"])
    assert chosen.language_code == "de"


def test_manual_captions_win_over_generated_when_falling_back():
    chosen = _select_transcript(
        FakeTranscriptList(
            [
                FakeTranscript("de", is_generated=True),
                FakeTranscript("fr", is_generated=False),
            ]
        ),
        ["tr"],
    )
    assert chosen.language_code == "fr"


def test_translatable_fallback_is_translated_to_the_preferred_language():
    chosen = _select_transcript(
        FakeTranscriptList([FakeTranscript("de", translations=("tr", "en"))]), ["tr", "en"]
    )
    assert chosen.language_code == "tr"


def test_failed_translation_keeps_the_raw_transcript():
    """Yanlis dilde transkript, hic transkript olmamasindan iyidir."""
    chosen = _select_transcript(
        FakeTranscriptList(
            [
                FakeTranscript(
                    "de",
                    translations=("tr",),
                    translate_error=yta.NotTranslatable(VIDEO_ID),
                )
            ]
        ),
        ["tr"],
    )
    assert chosen.language_code == "de"


def test_no_captions_at_all_still_raises():
    with pytest.raises(yta.NoTranscriptFound):
        _select_transcript(FakeTranscriptList([]), ["tr", "en"])


def test_provider_returns_foreign_transcript_end_to_end(tmp_path, monkeypatch):
    class FakeApi:
        def __init__(self, http_client=None, proxy_config=None):
            pass

        def list(self, video_id):
            return FakeTranscriptList([FakeTranscript("de")])

    monkeypatch.setattr(
        "src.providers.youtube_transcript_api_provider.YouTubeTranscriptApi", FakeApi
    )

    result = YouTubeTranscriptAPIProvider(_config(tmp_path)).fetch(VIDEO_ID, "tr")

    assert result is not None
    assert result.status == "available"
    assert result.language == "de"
    assert result.segments, "zaman damgalari korunmali"


def test_provider_still_reports_video_without_any_captions(tmp_path, monkeypatch):
    class FakeApi:
        def __init__(self, http_client=None, proxy_config=None):
            pass

        def list(self, video_id):
            return FakeTranscriptList([])

    monkeypatch.setattr(
        "src.providers.youtube_transcript_api_provider.YouTubeTranscriptApi", FakeApi
    )

    with pytest.raises(VideoUnavailableError):
        YouTubeTranscriptAPIProvider(_config(tmp_path)).fetch(VIDEO_ID, "tr")


# ------------------------------------------------------------ onbellek anahtari


def _cached(video_id="vid12345678", source="youtube_transcript_api", text="merhaba dunya"):
    return TranscriptResult(video_id=video_id, status="available", source=source, text=text)


def test_cache_hit_requires_matching_language(tmp_path):
    store = SQLiteStore(str(tmp_path / "app.db"))
    store.put_transcript_cache(_cached(), 600, "tr")

    assert store.get_transcript_cache("vid12345678", "youtube_transcript_api", "tr") is not None
    assert store.get_transcript_cache("vid12345678", "youtube_transcript_api", "en") is None


def test_cache_language_is_normalized(tmp_path):
    store = SQLiteStore(str(tmp_path / "app.db"))
    store.put_transcript_cache(_cached(), 600, "  TR ")

    assert store.get_transcript_cache("vid12345678", "youtube_transcript_api", "tr") is not None


def test_missing_language_hint_is_its_own_key(tmp_path):
    store = SQLiteStore(str(tmp_path / "app.db"))
    store.put_transcript_cache(_cached(), 600, None)

    assert store.get_transcript_cache("vid12345678", "youtube_transcript_api", None) is not None
    assert store.get_transcript_cache("vid12345678", "youtube_transcript_api", "en") is None


def test_language_column_is_added_to_an_existing_database(tmp_path):
    """Dil sutunu olmayan eski veritabani acilabilmeli."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE transcript_cache (
            video_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (video_id, provider)
        )
        """
    )
    conn.commit()
    conn.close()

    store = SQLiteStore(str(db_path))
    with store.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(transcript_cache)")}
    assert "language" in columns

    store.put_transcript_cache(_cached(), 600, "tr")
    assert store.get_transcript_cache("vid12345678", "youtube_transcript_api", "tr") is not None
