import functools
import inspect
import shutil
from typing import Optional

from src.config import AppConfig
# `parse_cookies_from_browser` burada TANIMLI DEGIL, yeniden ihrac ediliyor:
# tanimi `http_identity` icinde cunku artik yt-dlp disinda `requests` tarafinda
# da kullaniliyor. Bu modulden import eden mevcut cagri yerleri bozulmasin diye
# ad burada goruntude tutuluyor.
from src.utils.http_identity import DEFAULT_USER_AGENT, parse_cookies_from_browser

__all__ = [
    "build_ydl_common_options",
    "build_js_runtime_options",
    "js_runtime_warning",
    "parse_cookies_from_browser",
    "supports_js_runtimes",
]


@functools.lru_cache(maxsize=1)
def supports_js_runtimes() -> bool:
    """Kurulu yt-dlp `js_runtimes` secenegini taniyor mu?

    yt-dlp bilinmeyen secenekleri SESSIZCE yok sayar. 2025.08 sürümlerinde bu
    seçenek yoktu; kod onu gönderiyor, yt-dlp görmezden geliyor ve nsig çözümü
    saf-Python yorumlayıcıya düşüp başarısız oluyordu (indirilebilir format
    kalmıyordu). Artık desteklenmiyorsa açıkça bildiriyoruz.
    """
    try:
        import yt_dlp

        return "js_runtimes" in inspect.getsource(yt_dlp.YoutubeDL)
    except Exception:
        # Tespit edemezsek davranisi degistirme.
        return True


def build_ydl_common_options(config: AppConfig, *, need_media_formats: bool = False) -> dict:
    """Ortak yt-dlp secenekleri.

    `need_media_formats=True` ses/video indirmek icin gerekir. YouTube'un ses-only
    akislari DASH formatlaridir; `skip: ["dash","hls"]` bunlari da eledigi icin
    indirme "Requested format is not available" hatasiyla basarisiz oluyordu.
    Metadata ve altyazi cekiminde ise bu atlama ayristirmayi belirgin hizlandirir.
    """
    options = {
        "quiet": True,
        "no_warnings": True,
        # Indirme ilerleme cubugu stdout'u kirletiyor ve Streamlit loglarinda gurultu yaratiyor.
        "noprogress": True,
        # yt-dlp ile `requests` tarafi AYNI User-Agent'i tasimali: ayni kosuda
        # iki farkli istemci kimligi gormek dikkat cekicidir.
        "user_agent": DEFAULT_USER_AGENT,
    }
    if not need_media_formats:
        options["extractor_args"] = {"youtube": {"skip": ["dash", "hls"]}}
    if config.ffmpeg_path:
        options["ffmpeg_location"] = config.ffmpeg_path
    if config.ytdlp_proxy:
        options["proxy"] = config.ytdlp_proxy
    if config.ytdlp_cookies_file:
        options["cookiefile"] = config.ytdlp_cookies_file
    cookies_from_browser = parse_cookies_from_browser(config.ytdlp_cookies_from_browser)
    if cookies_from_browser:
        options["cookiesfrombrowser"] = cookies_from_browser
    js_runtimes = build_js_runtime_options(config.ytdlp_js_runtime)
    if js_runtimes and supports_js_runtimes():
        options["js_runtimes"] = js_runtimes
    return options


def js_runtime_warning(config: AppConfig) -> Optional[str]:
    """Yapilandirilmis JS runtime kullanilamiyorsa kullaniciya gosterilecek uyari."""
    if supports_js_runtimes():
        return None
    if not build_js_runtime_options(config.ytdlp_js_runtime):
        return None
    return (
        "Kurulu yt-dlp sürümü `js_runtimes` seçeneğini desteklemiyor; YouTube ses "
        "indirme başarısız olabilir. Güncelleyin: `pip install -U yt-dlp`"
    )


def build_js_runtime_options(runtime_name: Optional[str]) -> Optional[dict]:
    preferred = (runtime_name or "").strip().lower()
    candidates = [preferred] if preferred else ["node", "deno", "bun", "quickjs"]
    js_runtimes: dict[str, dict] = {}
    for candidate in candidates:
        if not candidate:
            continue
        path = shutil.which(candidate)
        if path:
            js_runtimes[candidate] = {"path": path}
            if preferred:
                break
    return js_runtimes or None
