"""`YTDLP_PROXY` ve cerezlerin YouTube'a giden HER istege ulastigini kilitler.

Bu dosyanin varlik sebebi: proxy uzun sure yalnizca yt-dlp'ye uygulaniyordu.
`youtube-transcript-api` parametresiz kuruluyor, yt-dlp'nin buldugu altyazi
dosyasi ise ciplak bir `requests.get` ile cekiliyordu. Ikisi de sunucunun
ciplak IP'sinden cikiyordu ve bunu gosteren hicbir test yoktu -- bu yuzden
kopukluk sessizce yasadi.
"""

from __future__ import annotations

import pytest

from src.config import AppConfig
from src.models import VideoCandidate
from src.providers import ytdlp_provider
from src.providers.youtube_transcript_api_provider import YouTubeTranscriptAPIProvider
from src.utils import http_identity
from src.utils.http_identity import DEFAULT_USER_AGENT, build_session, reset_identity_cache


NETSCAPE_COOKIES = "\n".join(
    [
        "# Netscape HTTP Cookie File",
        "\t".join([".youtube.com", "TRUE", "/", "TRUE", "2147483647", "CONSENT", "YES+42"]),
        "",
    ]
)


@pytest.fixture(autouse=True)
def _clear_identity_cache():
    # Kimlik onbellegi modul duzeyinde ve yapilandirmaya gore anahtarlaniyor;
    # testler arasinda sizmamali.
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
    return AppConfig(**values)


def test_session_carries_proxy_and_user_agent(tmp_path):
    config = _config(tmp_path, ytdlp_proxy="http://proxy.example:8080")
    with build_session(config) as session:
        assert session.proxies["http"] == "http://proxy.example:8080"
        assert session.proxies["https"] == "http://proxy.example:8080"
        assert session.headers["User-Agent"] == DEFAULT_USER_AGENT


def test_session_loads_netscape_cookie_file(tmp_path):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(NETSCAPE_COOKIES, encoding="utf-8")
    config = _config(tmp_path, ytdlp_cookies_file=str(cookie_file))

    with build_session(config) as session:
        assert session.cookies.get("CONSENT", domain=".youtube.com") == "YES+42"


def test_broken_cookie_file_degrades_instead_of_raising(tmp_path):
    # Cerezsiz devam etmek, transkript zincirini tamamen kapatmaktan iyidir.
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("bu bir cerez dosyasi degil", encoding="utf-8")
    config = _config(tmp_path, ytdlp_cookies_file=str(cookie_file))

    with build_session(config) as session:
        assert len(session.cookies) == 0


def test_each_session_is_a_fresh_object(tmp_path):
    """`youtube-transcript-api` thread basina ayri ornek istiyor.

    Session'lar paylasilirsa `MAX_TRANSCRIPT_WORKERS` thread'i ayni
    thread-safe-olmayan nesneyi kullanir.
    """
    config = _config(tmp_path, ytdlp_proxy="http://proxy.example:8080")
    with build_session(config) as first, build_session(config) as second:
        assert first is not second


def test_transcript_api_client_receives_configured_identity(tmp_path, monkeypatch):
    config = _config(tmp_path, ytdlp_proxy="http://proxy.example:8080")
    captured = {}

    class FakeApi:
        def __init__(self, http_client=None, proxy_config=None):
            captured["client"] = http_client

        def fetch(self, video_id, languages=None):
            raise AssertionError("bu test yalnizca istemci kurulumunu inceliyor")

    monkeypatch.setattr(
        "src.providers.youtube_transcript_api_provider.YouTubeTranscriptApi", FakeApi
    )

    YouTubeTranscriptAPIProvider(config)._api()

    client = captured["client"]
    assert client is not None, "http_client verilmezse proxy hicbir zaman uygulanmaz"
    assert client.proxies["https"] == "http://proxy.example:8080"
    assert client.headers["User-Agent"] == DEFAULT_USER_AGENT


def test_subtitle_download_goes_through_configured_session(tmp_path, monkeypatch):
    config = _config(tmp_path, ytdlp_proxy="http://proxy.example:8080")
    seen = {}

    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}
        text = "\n".join(
            [
                "WEBVTT",
                "",
                "00:00:01.000 --> 00:00:06.000",
                "yt-dlp altyazi yolu proxy uzerinden gecmeli ve bu metin yeterince uzun olmali",
                "",
            ]
        )

        def raise_for_status(self):
            return None

    class FakeSession:
        def __init__(self, inner):
            self._inner = inner

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, timeout=None):
            seen["proxies"] = dict(self._inner.proxies)
            seen["user_agent"] = self._inner.headers.get("User-Agent")
            return FakeResponse()

    def fake_build_session(cfg, logger=None):
        return FakeSession(build_session(cfg, logger))

    monkeypatch.setattr(ytdlp_provider, "build_session", fake_build_session)

    class FakeYdl:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            return {
                "automatic_captions": {
                    "en": [{"ext": "vtt", "url": "https://example.invalid/sub.vtt"}]
                }
            }

    monkeypatch.setattr(ytdlp_provider.yt_dlp, "YoutubeDL", FakeYdl)

    candidate = VideoCandidate(
        video_id="abc123def45",
        url="https://www.youtube.com/watch?v=abc123def45",
        title="t",
    )
    result = ytdlp_provider.YtDlpProvider(config).fetch_subtitles(
        candidate.url, candidate.video_id, "en"
    )

    assert result is not None and result.status == "available"
    assert seen["proxies"]["https"] == "http://proxy.example:8080"
    assert seen["user_agent"] == DEFAULT_USER_AGENT


def test_identity_is_cached_per_configuration(tmp_path, monkeypatch):
    """Cerez cozumu PAHALI; her istekte tekrarlanmamali."""
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(NETSCAPE_COOKIES, encoding="utf-8")
    config = _config(tmp_path, ytdlp_cookies_file=str(cookie_file))

    calls = {"n": 0}
    real_loader = http_identity._load_cookie_file

    def counting_loader(path, logger=None):
        calls["n"] += 1
        return real_loader(path, logger)

    monkeypatch.setattr(http_identity, "_load_cookie_file", counting_loader)

    build_session(config).close()
    build_session(config).close()
    build_session(config).close()

    assert calls["n"] == 1
