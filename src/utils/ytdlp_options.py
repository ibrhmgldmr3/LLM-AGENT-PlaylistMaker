import functools
import inspect
import shutil
from typing import Optional, Tuple

from src.config import AppConfig


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


def parse_cookies_from_browser(spec: Optional[str]) -> Optional[Tuple[str, ...]]:
    if not spec:
        return None
    value = spec.strip()
    if not value:
        return None
    parts = tuple(part for part in value.split(":") if part)
    if not parts:
        return None
    return parts


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
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
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
