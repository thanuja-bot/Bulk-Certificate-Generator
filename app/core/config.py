"""Typed application settings loaded from environment variables."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration; environment variables use the field names below."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    database_url: str | None = None
    storage_dir: Path = Path("./data/certificates")
    max_recipients: int = Field(default=5000, ge=1)
    worker_threads: int = Field(default=4, ge=1)
    worker_enabled: bool = True
    public_base_url: str = "http://localhost:5000"
    api_key: str | None = Field(default=None, min_length=1)
    log_level: str = "INFO"
    request_body_limit_bytes: int = Field(default=2_000_000, ge=1)

    @property
    def effective_database_url(self) -> str:
        """Return configured database URL or the local SQLite fallback."""
        if self.database_url:
            return self.database_url
        return "sqlite:///./data/app.db"


def get_settings() -> Settings:
    """Load settings for an app instance."""
    return Settings()
