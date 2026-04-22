from typing import Optional, Tuple

from src.config import AppConfig


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


def build_ydl_common_options(config: AppConfig) -> dict:
    options = {
        "quiet": True,
        "extractor_args": {"youtube": {"skip": ["dash", "hls"]}},
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
    }
    if config.ytdlp_proxy:
        options["proxy"] = config.ytdlp_proxy
    if config.ytdlp_cookies_file:
        options["cookiefile"] = config.ytdlp_cookies_file
    cookies_from_browser = parse_cookies_from_browser(config.ytdlp_cookies_from_browser)
    if cookies_from_browser:
        options["cookiesfrombrowser"] = cookies_from_browser
    return options
