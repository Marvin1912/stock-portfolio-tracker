"""Application configuration loaded from environment variables via pydantic-settings."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "get_settings"]


class Settings(BaseSettings):
    """Central configuration for the Stock Portfolio Tracker.

    All values are read from environment variables (or a .env file).
    Nested section comments map to the groups in .env.example.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    app_env: Literal["development", "staging", "production"] = "development"
    app_debug: bool = False
    secret_key: str = Field(..., min_length=32)
    allowed_hosts: list[AnyHttpUrl] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    database_url: str = Field(
        ...,
        description="Async SQLAlchemy URL — must use asyncpg driver.",
    )
    database_sync_url: str = Field(
        ...,
        description="Sync SQLAlchemy URL for Alembic migrations (psycopg2).",
    )

    # ------------------------------------------------------------------
    # Email / SMTP  (issue #20)
    # ------------------------------------------------------------------
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_address: str = "noreply@example.com"
    smtp_from_name: str = "Stock Portfolio Tracker"
    smtp_use_tls: bool = True

    # ------------------------------------------------------------------
    # Scheduler  (issue #21)
    # ------------------------------------------------------------------
    scheduler_timezone: str = "UTC"
    monthly_report_cron: str = "0 8 1 * *"

    # ------------------------------------------------------------------
    # Stock data provider
    # ------------------------------------------------------------------
    stock_api_key: str = ""
    stock_api_base_url: str = "https://api.example-stock-provider.com/v1"

    # ------------------------------------------------------------------
    # PDF import  (issue #16, #17)
    # ------------------------------------------------------------------
    pdf_upload_dir: Path = Path("/tmp/portfolio_uploads")
    pdf_max_upload_bytes: int = 20 * 1024 * 1024  # 20 MB

    # ------------------------------------------------------------------
    # Auth / JWT
    # ------------------------------------------------------------------
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60

    @field_validator("database_url")
    @classmethod
    def _require_asyncpg(cls, v: str) -> str:
        if "asyncpg" not in v:
            raise ValueError("database_url must use the asyncpg driver (postgresql+asyncpg://...)")
        return v

    @property
    def is_production(self) -> bool:
        """Return True when running in the production environment."""
        return self.app_env == "production"


def get_settings() -> Settings:
    """Return the application settings singleton.

    Import and call this function instead of instantiating Settings
    directly so that tests can override it via dependency injection.
    """
    return Settings()
