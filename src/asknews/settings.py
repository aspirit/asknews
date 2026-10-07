"""Runtime configuration, loaded once from the environment.

Only values that differ between deployments live here. Anything that is a
property of the product (chunk sizes, retrieval limits, prompts) is code or
domain configuration, not an environment variable.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: PostgresDsn = Field(
        validation_alias="DATABASE_URL",
        description="PostgreSQL connection URL; the database must have the pgvector extension.",
    )

    domain_dir: Path = Field(
        default=Path("domain"),
        validation_alias="ASKNEWS_DOMAIN_DIR",
        description="Directory holding domain configuration (speakers, curated lists).",
    )
    data_dir: Path = Field(
        default=Path("data"),
        validation_alias="ASKNEWS_DATA_DIR",
        description="Local working directory for fetched sources and caches.",
    )

    youtube_api_key: str | None = Field(
        default=None,
        validation_alias="YOUTUBE_API_KEY",
        description="Optional YouTube Data API key; without it only the channel feed is read.",
    )

    @property
    def database_dsn(self) -> str:
        return str(self.database_url)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, reading the environment on first use."""
    return Settings()  # type: ignore[call-arg]  # fields come from the environment
