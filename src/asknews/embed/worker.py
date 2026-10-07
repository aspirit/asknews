"""Embed segments that have no vector yet, or one from a different model."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Any

from asknews.db.connection import Connection, connect
from asknews.embed.base import Embedder, to_pgvector
from asknews.embed.select import embedder_from_settings

LOG = logging.getLogger(__name__)


def pending_segments(conn: Connection, model: str, *, limit: int) -> list[tuple[int, str]]:
    rows = conn.execute(
        """
        SELECT s.id, s.text
          FROM segments s
          LEFT JOIN segment_embeddings e ON e.segment_id = s.id
         WHERE e.segment_id IS NULL OR e.model <> %s
         ORDER BY s.id
         LIMIT %s
        """,
        (model, limit),
    ).fetchall()
    return [(int(r["id"]), str(r["text"])) for r in rows]


def write_embeddings(
    conn: Connection, model: str, ids: list[int], vectors: list[list[float]]
) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO segment_embeddings (segment_id, model, embedding)
            VALUES (%s, %s, %s::vector)
            ON CONFLICT (segment_id) DO UPDATE
               SET model = EXCLUDED.model, embedding = EXCLUDED.embedding
            """,
            [(sid, model, to_pgvector(vec)) for sid, vec in zip(ids, vectors, strict=True)],
        )


def embed_pending(
    conn: Connection, embedder: Embedder, *, batch_size: int = 64, limit: int | None = None
) -> int:
    """Embed segments in batches until none are pending. Returns the count done."""
    done = 0
    while limit is None or done < limit:
        size = batch_size if limit is None else min(batch_size, limit - done)
        batch = pending_segments(conn, embedder.model_name, limit=size)
        if not batch:
            break
        ids = [sid for sid, _ in batch]
        vectors = embedder.embed_documents([text for _, text in batch])
        write_embeddings(conn, embedder.model_name, ids, vectors)
        done += len(batch)
    return done


def run(args: argparse.Namespace) -> int:
    embedder = embedder_from_settings()
    started = time.monotonic()
    with connect(autocommit=True) as conn:
        done = embed_pending(conn, embedder, batch_size=args.batch, limit=args.limit or None)
    elapsed = time.monotonic() - started
    sys.stdout.write(f"{done} segments embedded with {embedder.model_name} in {elapsed:.1f}s\n")
    return 0


def add_cli(subparsers: Any) -> None:
    parser = subparsers.add_parser("embed", help="embed segments that lack a vector")
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--limit", type=int, default=0, help="stop after N segments")
    parser.set_defaults(func=run)
