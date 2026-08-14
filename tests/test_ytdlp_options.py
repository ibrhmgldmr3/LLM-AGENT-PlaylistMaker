from src.config import AppConfig
from src.utils import ytdlp_options
from src.utils.ytdlp_options import build_ydl_common_options, js_runtime_warning, parse_cookies_from_browser


def _config(**overrides):
    values = dict(gemini_api_key="test")
    values.update(overrides)
    return AppConfig(**values)


def test_metadata_extraction_skips_adaptive_formats_for_speed():
    options = build_ydl_common_options(_config())
    assert options["extractor_args"] == {"youtube": {"skip": ["dash", "hls"]}}


def test_media_download_must_not_skip_dash_formats():
    """Regresyon: YouTube'un ses-only akislari DASH'tir.

    `skip: ["dash","hls"]` bunlari da eledigi icin ses indirme
    "Requested format is not available" ile basarisiz oluyordu.
    """
    options = build_ydl_common_options(_config(), need_media_formats=True)
    assert "extractor_args" not in options


def test_progress_output_is_suppressed():
    options = build_ydl_common_options(_config())
    assert options["noprogress"] is True
    assert options["quiet"] is True


def test_js_runtime_is_omitted_when_unsupported(monkeypatch):
    """Regresyon: yt-dlp bilinmeyen secenekleri sessizce yok sayiyordu."""
    monkeypatch.setattr(ytdlp_options, "supports_js_runtimes", lambda: False)
    monkeypatch.setattr(ytdlp_options, "build_js_runtime_options", lambda name: {"node": {"path": "/usr/bin/node"}})

    options = build_ydl_common_options(_config(ytdlp_js_runtime="node"))

    assert "js_runtimes" not in options
    warning = js_runtime_warning(_config(ytdlp_js_runtime="node"))
    assert warning is not None and "pip install -U yt-dlp" in warning


def test_js_runtime_is_passed_when_supported(monkeypatch):
    monkeypatch.setattr(ytdlp_options, "supports_js_runtimes", lambda: True)
    monkeypatch.setattr(ytdlp_options, "build_js_runtime_options", lambda name: {"node": {"path": "/usr/bin/node"}})

    options = build_ydl_common_options(_config(ytdlp_js_runtime="node"))

    assert options["js_runtimes"] == {"node": {"path": "/usr/bin/node"}}
    assert js_runtime_warning(_config(ytdlp_js_runtime="node")) is None


def test_cookie_decryption_failure_is_a_config_error():
    """Regresyon: Chrome 127+ cerezleri okunamiyor (yt-dlp#10927).

    Bu hata gecici sayiliyordu; her video icin 3 kez tekrar deneniyor ve
    saglayici bosuna cooldown'a aliniyordu. Oysa tekrar denemek asla duzeltmez.
    """
    from src.providers.errors import ProviderPermanentError
    from src.providers.ytdlp_provider import _classify_ytdlp_error

    error = _classify_ytdlp_error(
        Exception("ERROR: Failed to decrypt with DPAPI. See https://github.com/yt-dlp/yt-dlp/issues/10927")
    )

    assert isinstance(error, ProviderPermanentError)
    assert "YTDLP_COOKIES_FROM_BROWSER" in str(error), "kullaniciya ne yapacagi soylenmeli"


def test_ordinary_network_error_stays_temporary():
    from src.providers.errors import ProviderTemporaryError
    from src.providers.ytdlp_provider import _classify_ytdlp_error

    assert isinstance(
        _classify_ytdlp_error(Exception("Connection reset by peer")), ProviderTemporaryError
    )


def test_video_level_error_is_not_confused_with_config():
    from src.providers.errors import VideoUnavailableError
    from src.providers.ytdlp_provider import _classify_ytdlp_error

    assert isinstance(
        _classify_ytdlp_error(Exception("ERROR: [youtube] abc: Private video")), VideoUnavailableError
    )


def test_cookie_spec_parsing():
    assert parse_cookies_from_browser("chrome") == ("chrome",)
    assert parse_cookies_from_browser("firefox:default") == ("firefox", "default")
    assert parse_cookies_from_browser("  ") is None
    assert parse_cookies_from_browser(None) is None
