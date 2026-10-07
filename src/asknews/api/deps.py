"""Shared runtime objects: connection pool, embedder, language model, config."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from fastapi import Request
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from asknews.answer.llm import LLM, ClaudeLLM, ScriptedLLM
from asknews.answer.pipeline import Limits
from asknews.db.connection import Connection
from asknews.domain_config import SpeakerDirectory, speakers
from asknews.embed.base import Embedder
from asknews.embed.select import embedder_from_settings
from asknews.retrieval.gate import Vocabulary, vocabulary
from asknews.settings import Settings, get_settings


@dataclass
class Runtime:
    settings: Settings
    pool: ConnectionPool
    embedder: Embedder
    llm: LLM
    directory: SpeakerDirectory
    vocab: Vocabulary
    limits: Limits

    def connection(self) -> Iterator[Connection]:
        with self.pool.connection() as conn:
            yield cast(Connection, conn)

    def close(self) -> None:
        self.pool.close()


def build_runtime(settings: Settings | None = None) -> Runtime:
    settings = settings or get_settings()
    pool = ConnectionPool(
        settings.database_dsn,
        min_size=1,
        max_size=8,
        kwargs={"row_factory": dict_row, "autocommit": True},
        open=True,
    )
    llm: LLM = (
        ClaudeLLM(settings.llm_model, effort=settings.llm_effort)
        if settings.llm == "claude"
        else ScriptedLLM()
    )
    return Runtime(
        settings=settings,
        pool=pool,
        embedder=embedder_from_settings(),
        llm=llm,
        directory=speakers(),
        vocab=vocabulary(),
        limits=Limits(daily_budget_usd=settings.daily_budget_usd),
    )


def runtime(request: Request) -> Runtime:
    rt: Runtime = request.app.state.runtime
    return rt


def connection(request: Request) -> Iterator[Connection]:
    yield from runtime(request).connection()


def client_hash(request: Request, salt: str) -> str:
    """A per-day hash of the client address: enough for rate limiting, no identity kept."""
    # nginx appends the connecting address to whatever the client sent, so the
    # last entry is the one it saw; the first could be anything.
    forwarded = request.headers.get("x-forwarded-for", "")
    address = forwarded.split(",")[-1].strip() or (request.client.host if request.client else "")
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    return hashlib.sha256(f"{salt}:{day}:{address}".encode()).hexdigest()[:32]
