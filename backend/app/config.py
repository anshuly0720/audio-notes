from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Looks for .env in backend/ first, then the repo root. Real env vars (Render) win over both.
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str
    gnani_api_key: str
    gemini_api_key: str = ""

    s3_endpoint: str = ""
    s3_key_id: str = ""
    s3_secret: str = ""
    s3_bucket: str = ""

    cors_origins: str = "http://localhost:3000"
    embed_worker: bool = True

    # Measured in the Day 0 spike: Gnani allows ~1 request/second, no concurrency
    asr_min_gap_seconds: float = 1.1

    # Demo caps to protect the free credits (~₹0.43 per audio minute)
    max_upload_mb: int = 500
    max_audio_minutes: int = 60
    upload_limit_per_hour: int = 10  # per client IP, protects ASR credits

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Read env once and reuse the same Settings object everywhere."""
    return Settings()