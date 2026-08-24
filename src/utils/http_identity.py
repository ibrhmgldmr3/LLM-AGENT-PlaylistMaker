from __future__ import annotations

import threading
from dataclasses import dataclass
from http.cookiejar import Cookie, CookieJar, LoadError, MozillaCookieJar
from typing import Optional

import requests

from src.config import AppConfig


# TEK tanim. `ytdlp_options` bunu ice aktariyor: iki ayri yerde yazilan bir
# User-Agent, birinin guncellenip digerinin kalmasi demekti ve YouTube'a ayni
# kosudan iki farkli istemci kimligi gitmesi tam olarak dikkat ceken sey.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def parse_cookies_from_browser(spec: Optional[str]) -> Optional[tuple[str, ...]]:
    """`YTDLP_COOKIES_FROM_BROWSER` degerini bilesenlerine ayirir.

    Bicim yt-dlp'nin `cookiesfrombrowser` seceneginin bekledigi sirayla
    `BROWSER:PROFILE:KEYRING:CONTAINER`. Bu modulde duruyor cunku artik iki
    tuketicisi var: yt-dlp secenekleri ve `requests` tarafi kimlik.
    """
    if not spec:
        return None
    value = spec.strip()
    if not value:
        return None
    parts = tuple(part for part in value.split(":") if part)
    if not parts:
        return None
    return parts


@dataclass(frozen=True)
class NetworkIdentity:
    """YouTube'a giden HER istegin tasimasi gereken kimlik.

    Cozumlenmesi PAHALI (tarayici cerez veritabaninin cozulmesi saniyeler
    surebilir), kullanilmasi ucuz. Bu yuzden kimlik onbellege aliniyor ama
    ondan uretilen `requests.Session` alinmiyor -- bkz. `build_session`.
    """

    proxies: Optional[dict[str, str]]
    cookies: tuple[Cookie, ...]
    user_agent: str

    @property
    def is_configured(self) -> bool:
        return bool(self.proxies or self.cookies)


_IDENTITY_CACHE: dict[tuple, NetworkIdentity] = {}
_IDENTITY_LOCK = threading.Lock()


class _QuietLogger:
    """yt-dlp'nin cerez cikaricisinin bekledigi gunlukcu yuzeyi.

    Kendi gunlukcumuze baglaniyor: varsayilan `YDLLogger` dogrudan stderr'e
    yaziyor ve API sunucusunun loglarini kirletiyor.
    """

    def __init__(self, logger=None):
        self._logger = logger

    def debug(self, message, *args, **kwargs):
        if self._logger:
            self._logger.debug("yt-dlp cookies: %s", message)

    info = debug

    def warning(self, message, *args, **kwargs):
        if self._logger:
            self._logger.warning("yt-dlp cookies: %s", message)

    def error(self, message, *args, **kwargs):
        if self._logger:
            self._logger.error("yt-dlp cookies: %s", message)


def _identity_key(config: AppConfig) -> tuple:
    return (
        (config.ytdlp_proxy or "").strip(),
        (config.ytdlp_cookies_file or "").strip(),
        (config.ytdlp_cookies_from_browser or "").strip(),
    )


def build_proxy_map(config: AppConfig) -> Optional[dict[str, str]]:
    """`requests` uyumlu proxy sozlugu.

    Tek bir `YTDLP_PROXY` degeri hem http hem https icin kullaniliyor: ayri
    ayri yapilandirilabilir yapmak, kullanicinin ikisini farkli birakip
    yalnizca birinin proxy'den gectigi bir durum uretirdi.
    """
    proxy = (config.ytdlp_proxy or "").strip()
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def _load_cookie_file(path: str, logger=None) -> tuple[Cookie, ...]:
    """Netscape bicimli cerez dosyasini okur.

    Okunamazsa BOS doner, hata FIRLATMAZ: cerezsiz devam etmek, transkript
    zincirini tamamen kapatmaktan iyidir. yt-dlp yolu ayni dosyayi kendi
    okuyor ve bozuksa `ProviderPermanentError` ile zaten yuksek sesle
    bildiriyor (bkz. `ytdlp_provider._CONFIG_ERROR_HINTS`).
    """
    jar = MozillaCookieJar(path)
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except (OSError, LoadError) as exc:
        if logger:
            logger.warning("Çerez dosyası okunamadı (%s): %s", path, exc)
        return ()
    return tuple(jar)


def _load_browser_cookies(spec: str, logger=None) -> tuple[Cookie, ...]:
    """Tarayici cerezlerini yt-dlp'nin cikaricisiyla alir.

    yt-dlp'ye BAGIMLI kaliniyor cunku tarayici cerez veritabanlarinin
    (Chrome App-Bound Encryption, Firefox profil kesfi, macOS keyring)
    cozumu bu projenin isi degil. Basarisiz olursa cerezsiz devam edilir:
    Chrome 127+ uzerinde bu cagri sik sik basarisiz oluyor ve orada asil
    kazanc zaten proxy'de.
    """
    parts = parse_cookies_from_browser(spec)
    if not parts:
        return ()
    try:
        from yt_dlp.cookies import extract_cookies_from_browser

        browser = parts[0]
        profile = parts[1] if len(parts) > 1 else None
        keyring = parts[2] if len(parts) > 2 else None
        container = parts[3] if len(parts) > 3 else None
        jar: CookieJar = extract_cookies_from_browser(
            browser,
            profile,
            _QuietLogger(logger),
            keyring=keyring,
            container=container,
        )
    except Exception as exc:  # cikarici cok cesitli hata turu firlatiyor
        if logger:
            logger.warning("Tarayıcı çerezleri okunamadı (%s): %s", spec, exc)
        return ()
    return tuple(jar)


def resolve_identity(config: AppConfig, logger=None) -> NetworkIdentity:
    """Yapilandirilmis ag kimligini cozer ve ONBELLEGE alir.

    Onbellek anahtari yapilandirmanin kendisi: farkli proxy/cerez ayarlariyla
    calisan iki yapilandirma birbirinin kimligini gormemeli.
    """
    key = _identity_key(config)
    with _IDENTITY_LOCK:
        cached = _IDENTITY_CACHE.get(key)
        if cached is not None:
            return cached

    proxy_value, cookie_file, browser_spec = key
    cookies: tuple[Cookie, ...] = ()
    if cookie_file:
        cookies = _load_cookie_file(cookie_file, logger)
    elif browser_spec:
        # Dosya ile tarayici AYNI ANDA kullanilmiyor: yt-dlp de dosyayi
        # onceliyor ve iki kaynagi birlestirmek, hangi cerezin kazandigi
        # belirsiz bir durum uretirdi.
        cookies = _load_browser_cookies(browser_spec, logger)

    identity = NetworkIdentity(
        proxies=build_proxy_map(config),
        cookies=cookies,
        user_agent=DEFAULT_USER_AGENT,
    )
    with _IDENTITY_LOCK:
        _IDENTITY_CACHE.setdefault(key, identity)
        return _IDENTITY_CACHE[key]


def build_session(config: AppConfig, logger=None) -> requests.Session:
    """Kimligi tasiyan TAZE bir `requests.Session` uretir.

    HER CAGRIDA YENI: `requests.Session` thread-safe degil ve
    `youtube-transcript-api` bunu belgesinde acikca soyluyor ("initialize an
    instance per thread"). Transkriptler `MAX_TRANSCRIPT_WORKERS` thread'inde
    paralel cekildigi icin paylasilan bir Session gercek bir yaris olurdu.
    Pahali olan kisim -- cerez cozumu -- `resolve_identity` icinde zaten
    onbellekte; burada kalan maliyet yalnizca nesne kurulumu.
    """
    identity = resolve_identity(config, logger)
    session = requests.Session()
    session.headers.update({"User-Agent": identity.user_agent})
    if identity.proxies:
        session.proxies.update(identity.proxies)
    for cookie in identity.cookies:
        session.cookies.set_cookie(cookie)
    return session


def reset_identity_cache() -> None:
    """Onbellegi bosaltir. Testler icin; uretimde yapilandirma sabit."""
    with _IDENTITY_LOCK:
        _IDENTITY_CACHE.clear()
