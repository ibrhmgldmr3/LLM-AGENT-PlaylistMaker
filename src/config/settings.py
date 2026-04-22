from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    gemini_api_key: str = Field(..., description="Gemini API key")
    gemini_model: str = Field(default="gemini-2.5-flash")

    youtube_data_api_key: str | None = Field(default=None)
    youtube_oauth_client_secret_file: str | None = Field(default=None)
    youtube_oauth_client_id: str | None = Field(default=None)
    youtube_oauth_client_secret: str | None = Field(default=None)
    youtube_oauth_token_file: str = Field(default="data/cache/youtube_oauth_token.json")

    asr_backend: str = Field(default="auto")
    faster_whisper_model_size: str = Field(default="base")
    faster_whisper_device: str = Field(default="cpu")
    faster_whisper_compute_type: str = Field(default="int8")
    whisper_cpp_cli_path: str | None = Field(default=None)
    whisper_cpp_model_path: str | None = Field(default=None)
    ffmpeg_path: str | None = Field(default=None)
    ytdlp_proxy: str | None = Field(default=None)
    ytdlp_cookies_from_browser: str | None = Field(default=None)
    ytdlp_cookies_file: str | None = Field(default=None)
    ytdlp_js_runtime: str | None = Field(default=None)
    allow_unsafe_openmp_workaround: bool = Field(default=True)

    data_dir: str = Field(default="data")
    sqlite_path: str = Field(default="data/cache/app.db")

    search_candidates_per_subtopic: int = Field(default=12)
    metadata_top_k: int = Field(default=4)
    request_timeout_sec: int = Field(default=30)
    retry_max_attempts: int = Field(default=3)
    retry_base_delay_sec: float = Field(default=1.0)

    search_cache_ttl_sec: int = Field(default=21600)
    metadata_cache_ttl_sec: int = Field(default=86400)
    transcript_cache_ttl_sec: int = Field(default=2592000)
    failure_cache_ttl_sec: int = Field(default=3600)
    provider_cooldown_sec: int = Field(default=900)

    youtube_playlist_privacy_status: str = Field(default="private")

    @field_validator("asr_backend")
    @classmethod
    def validate_asr_backend(cls, value: str) -> str:
        allowed = {"auto", "faster-whisper", "whisper.cpp"}
        if value not in allowed:
            raise ValueError(f"ASR_BACKEND must be one of {sorted(allowed)}")
        return value

    @field_validator("search_candidates_per_subtopic")
    @classmethod
    def validate_search_candidates_per_subtopic(cls, value: int) -> int:
        if not 10 <= value <= 20:
            raise ValueError("SEARCH_CANDIDATES_PER_SUBTOPIC must be between 10 and 20")
        return value

    @field_validator("metadata_top_k")
    @classmethod
    def validate_metadata_top_k(cls, value: int) -> int:
        if not 1 <= value <= 10:
            raise ValueError("METADATA_TOP_K must be between 1 and 10")
        return value

    @field_validator("request_timeout_sec", "retry_max_attempts")
    @classmethod
    def validate_positive_ints(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Value must be greater than 0")
        return value

    @field_validator("retry_base_delay_sec")
    @classmethod
    def validate_retry_base_delay_sec(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("RETRY_BASE_DELAY_SEC must be greater than 0")
        return value

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

    def whisper_cpp_ready(self) -> bool:
        return bool(self.whisper_cpp_cli_path and self.whisper_cpp_model_path)


def load_config() -> AppConfig:
    load_dotenv()
    values = {
        "gemini_api_key": os.getenv("GEMINI_API_KEY"),
        "gemini_model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        "youtube_data_api_key": os.getenv("YOUTUBE_DATA_API_KEY"),
        "youtube_oauth_client_secret_file": os.getenv("YOUTUBE_OAUTH_CLIENT_SECRET_FILE"),
        "youtube_oauth_client_id": os.getenv("YOUTUBE_OAUTH_CLIENT_ID"),
        "youtube_oauth_client_secret": os.getenv("YOUTUBE_OAUTH_CLIENT_SECRET"),
        "youtube_oauth_token_file": os.getenv("YOUTUBE_OAUTH_TOKEN_FILE", "data/cache/youtube_oauth_token.json"),
        "asr_backend": os.getenv("ASR_BACKEND", "auto"),
        "faster_whisper_model_size": os.getenv("FASTER_WHISPER_MODEL_SIZE", "base"),
        "faster_whisper_device": os.getenv("FASTER_WHISPER_DEVICE", "cpu"),
        "faster_whisper_compute_type": os.getenv("FASTER_WHISPER_COMPUTE_TYPE", "int8"),
        "whisper_cpp_cli_path": os.getenv("WHISPER_CPP_CLI_PATH"),
        "whisper_cpp_model_path": os.getenv("WHISPER_CPP_MODEL_PATH"),
        "ffmpeg_path": os.getenv("FFMPEG_PATH"),
        "ytdlp_proxy": os.getenv("YTDLP_PROXY"),
        "ytdlp_cookies_from_browser": os.getenv("YTDLP_COOKIES_FROM_BROWSER"),
        "ytdlp_cookies_file": os.getenv("YTDLP_COOKIES_FILE"),
        "ytdlp_js_runtime": os.getenv("YTDLP_JS_RUNTIME"),
        "allow_unsafe_openmp_workaround": os.getenv("ALLOW_UNSAFE_OPENMP_WORKAROUND", "true"),
        "data_dir": os.getenv("DATA_DIR", "data"),
        "sqlite_path": os.getenv("SQLITE_PATH", "data/cache/app.db"),
        "search_candidates_per_subtopic": os.getenv("SEARCH_CANDIDATES_PER_SUBTOPIC", "12"),
        "metadata_top_k": os.getenv("METADATA_TOP_K", "4"),
        "request_timeout_sec": os.getenv("REQUEST_TIMEOUT_SEC", "30"),
        "retry_max_attempts": os.getenv("RETRY_MAX_ATTEMPTS", "3"),
        "retry_base_delay_sec": os.getenv("RETRY_BASE_DELAY_SEC", "1.0"),
    }
    try:
        config = AppConfig.model_validate(values)
    except ValidationError as exc:
        raise RuntimeError(f"Invalid configuration: {exc}") from exc
    config.ensure_directories()
    return config
