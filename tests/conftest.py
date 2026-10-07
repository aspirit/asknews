"""Shared fixtures.

Database-backed tests run only when ``TEST_DATABASE_URL`` is set, and refuse
to run against a database whose name does not end in ``_test``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from asknews.answer.llm import ScriptedLLM
from asknews.answer.pipeline import Limits
from asknews.api.app import create_app
from asknews.api.deps import Runtime
from asknews.db.connection import Connection, connect
from asknews.db.migrate import migrate
from asknews.domain_config import load_speakers, load_topics
from asknews.embed.base import HashEmbedder
from asknews.embed.worker import embed_pending
from asknews.ingest.store import (
    Document,
    Media,
    Segment,
    replace_segments,
    upsert_document,
    upsert_media,
    upsert_speaker,
)
from asknews.retrieval.gate import load_vocabulary
from asknews.settings import Settings
from asknews.topics import tag_pending

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    if not urlparse(url).path.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must point at a database whose name ends with _test")
    return url


@pytest.fixture
def db(test_database_url: str) -> Iterator[Connection]:
    """A connection to an empty test database; the schema is dropped afterwards."""
    with connect(test_database_url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        yield conn
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")


# --- seeded application fixture shared by API and page tests ---


def seed_archive(db: Connection) -> dict[str, int]:
    migrate(db)
    powell = upsert_speaker(
        db, institution="fed", slug="powell", name="Jerome H. Powell", role="Chair"
    )
    ids: dict[str, int] = {}
    ids["statement"] = upsert_document(
        db,
        Document(
            "fed",
            "statement",
            "en",
            "20250917",
            "Federal Reserve issues FOMC statement",
            datetime(2025, 9, 17, 18, tzinfo=UTC),
            "https://fed.test/statement",
            body=(
                "Recent indicators suggest that growth of economic activity moderated in the "
                "first half of the year. Job gains have slowed, and the unemployment rate has "
                "edged up but remains low. Inflation has moved up and remains somewhat elevated. "
                "In support of its goals, the Committee decided to lower the target range for "
                "the federal funds rate by 1/4 percentage point to 4 to 4-1/4 percent."
            ),
        ),
    )
    replace_segments(
        db,
        ids["statement"],
        [
            Segment(
                0, "The Committee decided to lower the target range for the federal funds rate."
            ),
            Segment(1, "Inflation has moved up and remains somewhat elevated."),
        ],
    )
    ids["presser"] = upsert_document(
        db,
        Document(
            "fed",
            "press_conference",
            "en",
            "20250917",
            "FOMC Press Conference September 17, 2025",
            datetime(2025, 9, 17, 18, 30, tzinfo=UTC),
            "https://fed.test/pc",
            speaker_id=powell,
            meta={"timestamped": True},
        ),
    )
    replace_segments(
        db,
        ids["presser"],
        [
            Segment(
                0,
                "Good afternoon. We lowered the federal funds rate today.",
                speaker="Chair Powell",
                start_sec=14.5,
                end_sec=40.0,
            ),
        ],
    )
    upsert_media(db, ids["presser"], Media("brightcove", "6379710022112", 3132))
    ids["minutes"] = upsert_document(
        db,
        Document(
            "fed",
            "minutes",
            "en",
            "20250917",
            "Minutes of the Federal Open Market Committee",
            datetime(2025, 10, 8, tzinfo=UTC),
            "https://fed.test/minutes",
        ),
    )
    replace_segments(db, ids["minutes"], [Segment(0, "Participants discussed the labor market.")])
    embed_pending(db, HashEmbedder())
    tag_pending(db, load_topics(REPO_ROOT / "domain" / "topics.yaml"))
    return ids


@pytest.fixture
def api(
    db: Connection, test_database_url: str
) -> Iterator[tuple[TestClient, dict[str, int], ScriptedLLM]]:
    ids = seed_archive(db)
    llm = ScriptedLLM()
    pool: ConnectionPool = ConnectionPool(
        test_database_url,
        min_size=1,
        max_size=2,
        kwargs={"row_factory": dict_row, "autocommit": True},
        open=True,
    )
    settings = Settings(_env_file=None, DATABASE_URL=test_database_url)  # type: ignore[call-arg]
    runtime = Runtime(
        settings=settings,
        pool=pool,
        embedder=HashEmbedder(),
        llm=llm,
        directory=load_speakers(REPO_ROOT / "domain" / "speakers.yaml"),
        vocab=load_vocabulary(REPO_ROOT / "domain" / "gate.yaml"),
        limits=Limits(),
    )
    with TestClient(create_app(runtime)) as client:
        yield client, ids, llm
