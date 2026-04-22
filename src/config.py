from typing import Optional
from pydantic import BaseModel, Field, ValidationError, field_validator
from dotenv import load_dotenv
import os


class AppConfig(BaseModel):
    gemini_api_key: str = Field(..., alias="GEMINI_API_KEY")
    gemini_model: str = Field("gemini-1.5-flash", alias="GEMINI_MODEL")

    youtube_search_per_topic: int = Field(8, alias="YOUTUBE_SEARCH_PER_TOPIC")
    transcript_score_top_k: int = Field(3, alias="TRANSCRIPT_SCORE_TOP_K")

    request_timeout_sec: int = Field(30, alias="REQUEST_TIMEOUT_SEC")
    retry_max_attempts: int = Field(3, alias="RETRY_MAX_ATTEMPTS")
    retry_base_delay_sec: float = Field(1.0, alias="RETRY_BASE_DELAY_SEC")

    ffmpeg_path: Optional[str] = Field(None, alias="FFMPEG_PATH")
    whisper_cpp_cli_path: Optional[str] = Field(None, alias="WHISPER_CPP_CLI_PATH")
    whisper_cpp_model_path: Optional[str] = Field(None, alias="WHISPER_CPP_MODEL_PATH")
    ytdlp_proxy: Optional[str] = Field(None, alias="YTDLP_PROXY")
    ytdlp_cookies_from_browser: Optional[str] = Field(None, alias="YTDLP_COOKIES_FROM_BROWSER")
    ytdlp_cookies_file: Optional[str] = Field(None, alias="YTDLP_COOKIES_FILE")
    ytdlp_rate_limit_cooldown_sec: float = Field(20.0, alias="YTDLP_RATE_LIMIT_COOLDOWN_SEC")

    data_dir: str = Field("data", alias="DATA_DIR")

    @field_validator("youtube_search_per_topic")
    @classmethod
    def validate_search_per_topic(cls, v: int) -> int:
        if v < 8:
            raise ValueError("YOUTUBE_SEARCH_PER_TOPIC must be >= 8")
        return v

    @field_validator("transcript_score_top_k")
    @classmethod
    def validate_score_top_k(cls, v: int) -> int:
        if v < 1:
            raise ValueError("TRANSCRIPT_SCORE_TOP_K must be >= 1")
        return v

    @field_validator("request_timeout_sec")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("REQUEST_TIMEOUT_SEC must be > 0")
        return v

    @field_validator("retry_max_attempts")
    @classmethod
    def validate_retry_attempts(cls, v: int) -> int:
        if v < 1:
            raise ValueError("RETRY_MAX_ATTEMPTS must be >= 1")
        return v

    @field_validator("ytdlp_rate_limit_cooldown_sec")
    @classmethod
    def validate_rate_limit_cooldown(cls, v: float) -> float:
        if v < 0:
            raise ValueError("YTDLP_RATE_LIMIT_COOLDOWN_SEC must be >= 0")
        return v

    def ensure_whisper_config(self) -> None:
        if not self.whisper_cpp_cli_path or not self.whisper_cpp_model_path:
            raise ValueError("WHISPER_CPP_CLI_PATH and WHISPER_CPP_MODEL_PATH are required for whisper fallback")


def load_config() -> AppConfig:
    load_dotenv()
    env = dict(os.environ)
    # Backward compatibility for previous env naming.
    if not env.get("GEMINI_API_KEY"):
        env["GEMINI_API_KEY"] = env.get("GOOGLE_API_KEY") or env.get("OPENAI_API_KEY")
    if not env.get("GEMINI_MODEL"):
        env["GEMINI_MODEL"] = env.get("OPENAI_MODEL", "gemini-1.5-flash")
    try:
        return AppConfig(**env)
    except ValidationError as e:
        raise RuntimeError(
            "Invalid configuration. Set GEMINI_API_KEY (or GOOGLE_API_KEY / OPENAI_API_KEY as fallback). "
            f"Details: {e}"
        )
