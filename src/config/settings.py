from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values, find_dotenv, load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


# Ortam degiskeni -> model alani eslemesi.
# Varsayilan degerler SADECE modelde tanimlidir; burada tekrarlanmaz.
ENV_TO_FIELD: dict[str, str] = {
    "GEMINI_API_KEY": "gemini_api_key",
    "GEMINI_MODEL": "gemini_model",
    "GEMINI_THINKING_BUDGET": "gemini_thinking_budget",
    "YOUTUBE_DATA_API_KEY": "youtube_data_api_key",
    "YOUTUBE_OAUTH_CLIENT_SECRET_FILE": "youtube_oauth_client_secret_file",
    "YOUTUBE_OAUTH_CLIENT_ID": "youtube_oauth_client_id",
    "YOUTUBE_OAUTH_CLIENT_SECRET": "youtube_oauth_client_secret",
    "YOUTUBE_OAUTH_TOKEN_FILE": "youtube_oauth_token_file",
    "YOUTUBE_OAUTH_ALLOW_LOCAL_SERVER": "youtube_oauth_allow_local_server",
    "YOUTUBE_PLAYLIST_PRIVACY_STATUS": "youtube_playlist_privacy_status",
    "ASR_BACKEND": "asr_backend",
    "FASTER_WHISPER_MODEL_SIZE": "faster_whisper_model_size",
    "FASTER_WHISPER_DEVICE": "faster_whisper_device",
    "FASTER_WHISPER_COMPUTE_TYPE": "faster_whisper_compute_type",
    "FASTER_WHISPER_BEAM_SIZE": "faster_whisper_beam_size",
    "WHISPER_CPP_CLI_PATH": "whisper_cpp_cli_path",
    "WHISPER_CPP_MODEL_PATH": "whisper_cpp_model_path",
    "WHISPER_CPP_TIMEOUT_SEC": "whisper_cpp_timeout_sec",
    "FFMPEG_PATH": "ffmpeg_path",
    "YTDLP_PROXY": "ytdlp_proxy",
    "YTDLP_COOKIES_FROM_BROWSER": "ytdlp_cookies_from_browser",
    "YTDLP_COOKIES_FILE": "ytdlp_cookies_file",
    "YTDLP_JS_RUNTIME": "ytdlp_js_runtime",
    "ALLOW_UNSAFE_OPENMP_WORKAROUND": "allow_unsafe_openmp_workaround",
    "DATA_DIR": "data_dir",
    "SQLITE_PATH": "sqlite_path",
    "SECRET_ENCRYPTION_KEY": "secret_encryption_key",
    "AUTH_MODE": "auth_mode",
    "SESSION_TTL_SEC": "session_ttl_sec",
    "INCLUDE_ENGLISH_BY_DEFAULT": "include_english_by_default",
    "MAX_SUBTOPICS": "max_subtopics",
    "SEARCH_CANDIDATES_PER_SUBTOPIC": "search_candidates_per_subtopic",
    "METADATA_TOP_K": "metadata_top_k",
    "TRANSCRIPT_ENRICHMENT_TOP_K": "transcript_enrichment_top_k",
    "ENABLE_ASR_FALLBACK": "enable_asr_fallback",
    "MAX_ASR_VIDEOS_PER_RUN": "max_asr_videos_per_run",
    "CHANNEL_REPEAT_PENALTY": "channel_repeat_penalty",
    "MAX_SEARCH_WORKERS": "max_search_workers",
    "MAX_TRANSCRIPT_WORKERS": "max_transcript_workers",
    "REQUEST_TIMEOUT_SEC": "request_timeout_sec",
    "RETRY_MAX_ATTEMPTS": "retry_max_attempts",
    "RETRY_BASE_DELAY_SEC": "retry_base_delay_sec",
    "SEARCH_CACHE_TTL_SEC": "search_cache_ttl_sec",
    "TRANSCRIPT_CACHE_TTL_SEC": "transcript_cache_ttl_sec",
    "FAILURE_CACHE_TTL_SEC": "failure_cache_ttl_sec",
    "PROVIDER_COOLDOWN_SEC": "provider_cooldown_sec",
    "RATE_LIMIT_COOLDOWN_SEC": "rate_limit_cooldown_sec",
    "PROVIDER_FAILURE_THRESHOLD": "provider_failure_threshold",
}


class UserCredentials(BaseModel):
    """KULLANICIYA ait sirlar.

    Cok kullanicili kuruluma gecildiginde bu grup `.env`'den cikip kullanici
    basina (sifreli) saklanacak. YouTube Data API kotasi PROJE basina gunde
    10.000 birim ve bir calistirma ~1.200 birim tuketiyor; yani paylasimli
    anahtarla gunde ~8 calistirma yapilabilir. Bu yuzden cok kullanicili modelde
    her kullanici kendi anahtarini getirir (BYOK).

    Bu degerler ASLA API yanitinda donmemeli.
    """

    model_config = ConfigDict(extra="ignore")

    gemini_api_key: str = Field(..., description="Gemini API key")
    youtube_data_api_key: str | None = Field(default=None)
    youtube_oauth_token_file: str = Field(default="data/cache/youtube_oauth_token.json")
    ytdlp_proxy: str | None = Field(default=None)
    ytdlp_cookies_from_browser: str | None = Field(default=None)
    ytdlp_cookies_file: str | None = Field(default=None)


class RunOptions(BaseModel):
    """Calistirma basina degisebilen secenekler.

    Streamlit'te kenar cubugu, API'de istek govdesi. Sunucu ayari DEGIL:
    iki kullanici ayni sunucuda farkli degerlerle calistirabilmeli.
    """

    model_config = ConfigDict(extra="ignore")

    gemini_model: str = Field(default="gemini-3.7-flash")
    youtube_playlist_privacy_status: str = Field(default="private")
    # Ana dil disinda Ingilizce arama da yapilsin mi (arayuzdeki anahtarin
    # varsayilani). Alt konu basina bir ek `search.list` cagrisi = +100 kota birimi.
    include_english_by_default: bool = Field(default=True)
    max_subtopics: int = Field(default=6)
    search_candidates_per_subtopic: int = Field(default=12)
    metadata_top_k: int = Field(default=4)
    transcript_enrichment_top_k: int = Field(default=2)
    # VARSAYILAN KAPALI. ASR bir videoyu indirip Whisper'dan gecirir; olculen maliyet
    # 11 dakikalik bir video icin ~90-120 sn (CPU, base model). Ilk iki transkript
    # saglayicisi vakalarin buyuk cogunlugunu zaten karsiliyor ve transkriptin
    # siralamaya katkisi en fazla +1.0 puan. Arayuzden acilabilir.
    enable_asr_fallback: bool = Field(default=False)
    max_asr_videos_per_run: int = Field(default=2)
    # Ayni kanaldan tekrar secim yapmanin bedeli. Kucuk tutuldugu icin yalnizca
    # yakin skorlu adaylarda belirleyici olur; playlist'in tek kanala saplanmasini
    # engeller ama acikca daha iyi bir videoyu elemez.
    channel_repeat_penalty: float = Field(default=0.6)

    @field_validator("youtube_playlist_privacy_status")
    @classmethod
    def validate_privacy_status(cls, value: str) -> str:
        allowed = {"private", "unlisted", "public"}
        if value not in allowed:
            raise ValueError(f"YOUTUBE_PLAYLIST_PRIVACY_STATUS must be one of {sorted(allowed)}")
        return value

    @field_validator("max_subtopics")
    @classmethod
    def validate_max_subtopics(cls, value: int) -> int:
        if not 1 <= value <= 20:
            raise ValueError("MAX_SUBTOPICS must be between 1 and 20")
        return value

    @field_validator("search_candidates_per_subtopic")
    @classmethod
    def validate_search_candidates_per_subtopic(cls, value: int) -> int:
        if not 10 <= value <= 50:
            raise ValueError("SEARCH_CANDIDATES_PER_SUBTOPIC must be between 10 and 50")
        return value

    @field_validator("metadata_top_k")
    @classmethod
    def validate_metadata_top_k(cls, value: int) -> int:
        if not 1 <= value <= 10:
            raise ValueError("METADATA_TOP_K must be between 1 and 10")
        return value

    @field_validator("transcript_enrichment_top_k")
    @classmethod
    def validate_enrichment_top_k(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("TRANSCRIPT_ENRICHMENT_TOP_K must be greater than 0")
        return value

    @field_validator("max_asr_videos_per_run")
    @classmethod
    def validate_max_asr_videos(cls, value: int) -> int:
        if value < 0:
            raise ValueError("MAX_ASR_VIDEOS_PER_RUN must be zero or greater")
        return value


class ServerConfig(BaseModel):
    """Kurulumun tamamina ait ayarlar; `.env`'de kalir.

    Yollar, zaman asimlari, worker sayilari, ASR arka uclari, onbellek TTL'leri.
    Kullaniciya gore degismez.
    """

    model_config = ConfigDict(extra="ignore")

    # Negatif = ayari hic gonderme (VARSAYILAN). 0 = "thinking" kapali.
    # Olcumde `thinking_budget=0` kabul eden modelde hiz kazandirmadi, kabul
    # etmeyen modelleri ise 400 ile tamamen kirdi.
    gemini_thinking_budget: int = Field(default=-1)
    youtube_oauth_allow_local_server: bool = Field(default=True)

    asr_backend: str = Field(default="auto")
    faster_whisper_model_size: str = Field(default="base")
    faster_whisper_device: str = Field(default="cpu")
    faster_whisper_compute_type: str = Field(default="int8")
    faster_whisper_beam_size: int = Field(default=1)
    whisper_cpp_cli_path: str | None = Field(default=None)
    whisper_cpp_model_path: str | None = Field(default=None)
    whisper_cpp_timeout_sec: int = Field(default=1800)
    ffmpeg_path: str | None = Field(default=None)
    ytdlp_js_runtime: str | None = Field(default=None)
    allow_unsafe_openmp_workaround: bool = Field(default=True)

    data_dir: str = Field(default="data")
    sqlite_path: str = Field(default="data/cache/app.db")
    # Saklanan sirlari (OAuth jetonlari) sifrelemek icin kullanilir. Tanimsizsa
    # jetonlar duz metin yazilir. `python -m src.storage.crypto` ile uretilebilir.
    secret_encryption_key: str | None = Field(default=None)

    # `single_user` (VARSAYILAN): her istek `DEFAULT_USER_ID`'ye ait, anahtarlar
    # `.env`'den okunur. Bugunku kurulum ve gelistirme akisi boyle calisiyor.
    #
    # `multi_user`: istek bir oturum cerezi tasimali, anahtarlar KULLANICI
    # BASINA veritabanindan okunur. `.env` anahtarlari bu modda YEDEK OLARAK
    # KULLANILMAZ -- kullanilsaydi anahtarini girmeyen bir kullanici sessizce
    # kurulum sahibinin YouTube kotasini harcardi (kota proje basina gunde
    # 10.000 birim ve bir calistirma ~1.200 birim).
    auth_mode: Literal["single_user", "multi_user"] = Field(default="single_user")

    # OAuth ISTEMCISI kuruluma ait, kullaniciya degil: uygulamanin Google'a
    # kayitli kimligi bu. Kullanici basina olsaydi herkesin kendi Google Cloud
    # OAuth istemcisini acip kendi yonlendirme adresini eklemesi gerekirdi.
    # Kullanicidan gelen sey JETON (yayin izni), istemci degil.
    youtube_oauth_client_secret_file: str | None = Field(default=None)
    youtube_oauth_client_id: str | None = Field(default=None)
    youtube_oauth_client_secret: str | None = Field(default=None)

    # Oturum omru. Varsayilan 14 gun: kullaniciyi her gun Google'a geri
    # gondermeyecek kadar uzun, calinan bir cerezin suresiz gecerli olmayacagi
    # kadar kisa.
    session_ttl_sec: int = Field(default=14 * 24 * 3600, ge=300)

    max_search_workers: int = Field(default=4)
    max_transcript_workers: int = Field(default=4)
    request_timeout_sec: int = Field(default=30)
    retry_max_attempts: int = Field(default=3)
    retry_base_delay_sec: float = Field(default=1.0)

    search_cache_ttl_sec: int = Field(default=21600)
    transcript_cache_ttl_sec: int = Field(default=2592000)
    failure_cache_ttl_sec: int = Field(default=900)
    provider_cooldown_sec: int = Field(default=900)
    # HTTP 429 / IP blogu icin ayri (ve daha uzun) dinlenme suresi. YouTube'un
    # hiz sinirlari dakikalarca surer; 15 dk sonra tekrar denemek bogmaya devam eder.
    rate_limit_cooldown_sec: int = Field(default=1800)
    provider_failure_threshold: int = Field(default=3)

    @field_validator("asr_backend")
    @classmethod
    def validate_asr_backend_server(cls, value: str) -> str:
        allowed = {"auto", "faster-whisper", "whisper.cpp"}
        if value not in allowed:
            raise ValueError(f"ASR_BACKEND must be one of {sorted(allowed)}")
        return value

    @field_validator(
        "request_timeout_sec",
        "retry_max_attempts",
        "whisper_cpp_timeout_sec",
        "provider_failure_threshold",
        "max_search_workers",
        "max_transcript_workers",
        "faster_whisper_beam_size",
    )
    @classmethod
    def validate_positive_ints_server(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Value must be greater than 0")
        return value

    @field_validator(
        "search_cache_ttl_sec",
        "transcript_cache_ttl_sec",
        "failure_cache_ttl_sec",
        "provider_cooldown_sec",
        "rate_limit_cooldown_sec",
    )
    @classmethod
    def validate_non_negative_ints_server(cls, value: int) -> int:
        if value < 0:
            raise ValueError("Value must be zero or greater")
        return value

    @field_validator("retry_base_delay_sec")
    @classmethod
    def validate_retry_base_delay_sec(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("RETRY_BASE_DELAY_SEC must be greater than 0")
        return value


class AppConfig(ServerConfig, UserCredentials, RunOptions):
    """Servislere gecirilen BIRLESIK gorunum.

    Uc kaynagin (sunucu ayari + kullanici sirri + calistirma secenegi) tek bir
    nesnede birlesmis hali. Servis ve saglayici katmani bu ayrimi hic gormez —
    tek bir config alir, bugunku gibi.

    Cok kullanicili moda gecildiginde `compose()` her istek icin farkli bir
    kullanici/secenek bilesimiyle cagrilir; `.env` yalnizca sunucu ayarlarini
    besler.
    """

    model_config = ConfigDict(extra="ignore")

    # load_config tarafindan doldurulur; kullaniciya gosterilecek yapilandirma uyarilari.
    config_warnings: list[str] = Field(default_factory=list)

    @classmethod
    def compose(
        cls,
        server: ServerConfig,
        credentials: UserCredentials,
        options: RunOptions,
        config_warnings: list[str] | None = None,
    ) -> "AppConfig":
        """Uc kaynagi tek bir calisma yapilandirmasina birlestirir."""
        return cls.model_validate(
            {
                **server.model_dump(),
                **credentials.model_dump(),
                **options.model_dump(),
                "config_warnings": config_warnings or [],
            }
        )

    def server_config(self) -> ServerConfig:
        return ServerConfig.model_validate(self.model_dump())

    def credentials(self) -> UserCredentials:
        return UserCredentials.model_validate(self.model_dump())

    def run_options(self) -> RunOptions:
        return RunOptions.model_validate(self.model_dump())

    def public_capabilities(self) -> dict[str, bool]:
        """Arayuzun hangi kontrolleri acabilecegini anlatir. SIR ICERMEZ.

        `GET /api/config` bunu doner; anahtarlarin kendisi asla disari cikmaz.
        """
        return {
            "gemini_configured": bool(self.gemini_api_key),
            "youtube_search_configured": bool(self.youtube_data_api_key),
            "youtube_publish_configured": bool(
                self.youtube_oauth_client_secret_file
                or (self.youtube_oauth_client_id and self.youtube_oauth_client_secret)
            ),
            "asr_available": self.asr_backend != "auto" or bool(self.whisper_cpp_cli_path),
            "cookies_configured": bool(self.ytdlp_cookies_from_browser or self.ytdlp_cookies_file),
        }

    # NOT: Dogrulayicilar artik alanlarin ait oldugu alt modellerde
    # (ServerConfig / UserCredentials / RunOptions) tanimli ve kalitimla geliyor.

    @property
    def runs_dir(self) -> Path:
        return Path(self.data_dir) / "runs"

    @property
    def cache_dir(self) -> Path:
        return Path(self.data_dir) / "cache"

    def ensure_directories(self) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        Path(self.sqlite_path).parent.mkdir(parents=True, exist_ok=True)

    def effective_enrichment_top_k(self) -> int:
        """Transkript zenginlestirmesi kisa listenin disina tasmamali."""
        return max(1, min(self.transcript_enrichment_top_k, self.metadata_top_k))


def _check_dotenv_encoding(dotenv_path: str) -> None:
    """`.env` icinde null bayt varsa anlasilir bir hata verir.

    PowerShell'in `>>` operatoru dosyaya UTF-16 yazar; UTF-8 bir `.env`'e boyle
    bir satir eklendiginde `python-dotenv` `ValueError: embedded null character`
    ile patlar ve yigin izi sorunun nedenini hic anlatmaz.
    """
    try:
        with open(dotenv_path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return
    if b"\x00" not in raw:
        return

    damaged = [
        str(index)
        for index, line in enumerate(raw.split(b"\n"), start=1)
        if b"\x00" in line
    ]
    raise RuntimeError(
        f"`.env` dosyası bozuk: {len(damaged)} satırda null bayt var "
        f"(satır {', '.join(damaged[:5])}). Dosyaya UTF-16 kodlu bir satır eklenmiş; "
        "PowerShell'de `>>` veya `Out-File` kullanıldığında bu olur.\n"
        "Düzeltmek için o satırları silip UTF-8 olarak yeniden yazın, ya da "
        "PowerShell'de şunu kullanın:\n"
        '  Add-Content .env "ANAHTAR=deger" -Encoding utf8'
    )


def _collect_env_warnings() -> list[str]:
    """`.env` icindeki taninmayan anahtarlari bildirir.

    `extra="ignore"` yazim hatalarini sessizce yutuyordu; artik kullaniciya
    "bu ayari yazdim ama hicbir sey degismedi" durumunu acikca soyluyoruz.
    """
    dotenv_path = find_dotenv(usecwd=True)
    if not dotenv_path:
        return []
    try:
        entries = dotenv_values(dotenv_path)
    except Exception:
        return []
    unknown = sorted(key for key in entries if key and key not in ENV_TO_FIELD)
    if not unknown:
        return []
    return [
        f"`.env` içinde tanınmayan anahtar(lar) yok sayıldı: {', '.join(unknown)}",
    ]


def load_config() -> AppConfig:
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        _check_dotenv_encoding(dotenv_path)
    try:
        # Cozulen yolu ACIKCA gecir: kontrol ettigimiz dosya ile yuklenen dosya
        # ayni olsun (ve testler gecici bir `.env`'i hedefleyebilsin).
        load_dotenv(dotenv_path) if dotenv_path else load_dotenv()
    except ValueError as exc:
        raise RuntimeError(
            f"`.env` dosyası okunamadı ({exc}). Dosyanın UTF-8 kodlu olduğundan emin olun."
        ) from exc
    values: dict[str, str] = {}
    for env_name, field_name in ENV_TO_FIELD.items():
        raw = os.getenv(env_name)
        if raw is None:
            continue
        raw = raw.strip()
        if raw == "":
            # Bos birakilan opsiyonel degisken, modeldeki varsayilani ezmemeli.
            continue
        values[field_name] = raw

    warnings = _collect_env_warnings()
    try:
        config = AppConfig.model_validate({**values, "config_warnings": warnings})
    except ValidationError as exc:
        if "gemini_api_key" in str(exc):
            raise RuntimeError(
                "GEMINI_API_KEY tanımlı değil. `.env` dosyanıza ekleyin (örnek için `.env.example`)."
            ) from exc
        raise RuntimeError(f"Invalid configuration: {exc}") from exc
    config.ensure_directories()
    return config
