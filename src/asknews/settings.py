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
        default=PostgresDsn("postgresql://asknews:asknews@localhost:5432/asknews"),
        validation_alias="DATABASE_URL",
        description=(
            "PostgreSQL connection URL with the pgvector extension; the default is the "
            "development instance from docker-compose.yml, production sets its own."
        ),
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

    embedder: str = Field(
        default="hash",
        validation_alias="ASKNEWS_EMBEDDER",
        description=(
            "hash (deterministic stand-in), remote (embedding service) or local (in-process model)."
        ),
    )
    embed_model: str = Field(
        default="google/embeddinggemma-300m",
        validation_alias="ASKNEWS_EMBED_MODEL",
        description=(
            "sentence-transformers model for embedder=local and for the embedding service; "
            "google/embeddinggemma-300m (gated) or intfloat/multilingual-e5-base (open)."
        ),
    )
    embed_url: str = Field(
        default="http://127.0.0.1:8201",
        validation_alias="ASKNEWS_EMBED_URL",
        description="Base URL of the embedding service when embedder=remote.",
    )
    llm: str = Field(
        default="claude",
        validation_alias="ASKNEWS_LLM",
        description=(
            "claude (Anthropic API, credentials from the environment) or scripted (offline)."
        ),
    )
    llm_model: str = Field(default="claude-opus-5-5", validation_alias="ASKNEWS_LLM_MODEL")
    llm_effort: str = Field(default="low", validation_alias="ASKNEWS_LLM_EFFORT")
    summary_model: str = Field(
        default="claude-sonnet-5-5",
        validation_alias="ASKNEWS_SUMMARY_MODEL",
        description="Model for bulk generation: document summaries, translations and briefs.",
    )
    daily_budget_usd: float = Field(default=3.0, validation_alias="ASKNEWS_DAILY_BUDGET_USD")
    client_salt: str = Field(
        default="change-me",
        validation_alias="ASKNEWS_CLIENT_SALT",
        description="Salt for the daily client hash used in rate limiting.",
    )
    site_url: str = Field(
        default="https://asknews.jp",
        validation_alias="ASKNEWS_SITE_URL",
        description="Public origin, used for canonical and alternate-language links.",
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
    return Settings()
