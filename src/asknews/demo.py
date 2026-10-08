"""Small, synthetic archive for local runs without publisher or model access."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Literal

import yaml
from psycopg.conninfo import conninfo_to_dict
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from asknews.db.connection import Connection, connect
from asknews.db.migrate import migrate
from asknews.domain_config import load_topics
from asknews.embed.base import HashEmbedder
from asknews.embed.worker import embed_pending
from asknews.ingest.store import Document, Segment, replace_segments, upsert_document
from asknews.topics import tag_pending

DEFAULT_DSN = "postgresql://asknews:asknews@127.0.0.1:55432/asknews_demo"
FIXTURE_VERSION = "demo-v1"


class DemoSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)
    speaker: str | None = None
    start_sec: float | None = Field(default=None, ge=0)
    end_sec: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def valid_timing(self) -> DemoSegment:
        if self.end_sec is not None and (self.start_sec is None or self.end_sec < self.start_sec):
            raise ValueError("end_sec needs a start_sec no later than it")
        return self


class DemoDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    institution: Literal["fed", "boj"]
    kind: Literal["statement", "press_conference", "minutes", "report"]
    lang: Literal["en", "ja"]
    external_id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    title: str = Field(pattern=r"^\[Demo\] .+")
    published_at: AwareDatetime
    segments: list[DemoSegment]


class Fixture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["demo-v1"]
    documents: list[DemoDocument] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_documents(self) -> Fixture:
        keys = [(d.institution, d.kind, d.external_id) for d in self.documents]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate demo document")
        return self


def load_fixture(path: Path) -> Fixture:
    return Fixture.model_validate(yaml.safe_load(path.read_text("utf-8")))


def validate_destination(dsn: str) -> None:
    parts = conninfo_to_dict(dsn)
    if (
        parts.get("dbname") != "asknews_demo"
        or parts.get("host") not in {"127.0.0.1", "localhost", "::1"}
        or parts.get("hostaddr") not in {None, "127.0.0.1", "::1"}
        or "service" in parts
    ):
        raise ValueError("demo data requires the local asknews_demo database")


def seed(conn: Connection, fixture: Fixture, *, domain: Path) -> dict[str, int]:
    """Seed only an empty archive or this fixture's own data; never truncate it."""
    exists = conn.execute("SELECT to_regclass('public.documents') AS name").fetchone()
    if exists and exists["name"] is not None:
        foreign = conn.execute(
            "SELECT 1 FROM documents WHERE meta->>'demo_fixture' IS DISTINCT FROM %s LIMIT 1",
            (FIXTURE_VERSION,),
        ).fetchone()
        if foreign:
            raise ValueError("refusing to mix demo documents with another corpus")
    migrate(conn)
    ids: dict[str, int] = {}
    with conn.transaction():
        for item in fixture.documents:
            segments = [Segment(i, **s.model_dump()) for i, s in enumerate(item.segments)]
            key = f"{item.institution}/{item.kind}/{item.external_id}"
            doc_id = upsert_document(
                conn,
                Document(
                    institution=item.institution,
                    kind=item.kind,
                    lang=item.lang,
                    external_id=item.external_id,
                    title=item.title,
                    published_at=item.published_at,
                    source_url=f"https://example.invalid/demo/{key}",
                    body="\n\n".join(s.text for s in segments) or None,
                    meta={
                        "demo_fixture": FIXTURE_VERSION,
                        **({"text": "unreadable"} if not segments else {}),
                    },
                ),
            )
            replace_segments(conn, doc_id, segments)
            ids[key] = doc_id
        embed_pending(conn, HashEmbedder())
        tag_pending(conn, load_topics(domain / "topics.yaml"))
    return ids


def run(args: argparse.Namespace) -> int:
    try:
        validate_destination(args.dsn)
        domain = Path("domain")
        fixture = load_fixture(domain / "demo.yaml")
        # Do not let libpq environment defaults redirect a localhost DSN.
        parts = conninfo_to_dict(args.dsn)
        address = "::1" if parts["host"] == "::1" else "127.0.0.1"
        with connect(args.dsn, hostaddr=address, autocommit=True) as conn:
            actual = conn.execute("SELECT current_database() AS name").fetchone()
            if actual is None or actual["name"] != "asknews_demo":
                sys.stderr.write("demo: unexpected database; demo seed was not run\n")
                return 2
            ids = seed(conn, fixture, domain=domain)
    except ValueError as exc:
        sys.stderr.write(f"demo: {exc}\n")
        return 2
    sys.stdout.write(
        f"{len(ids)} synthetic demo documents ready (hash embeddings; no model calls).\n"
    )
    for key, doc_id in ids.items():
        sys.stdout.write(f"  /doc/{doc_id}  {key}\n")
    return 0


def add_cli(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "demo-seed", help="seed the isolated local demonstration database"
    )
    parser.add_argument("--dsn", default=DEFAULT_DSN, help="local asknews_demo connection URL")
    parser.set_defaults(func=run)
