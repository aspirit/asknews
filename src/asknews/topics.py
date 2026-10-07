"""Topic membership: which documents mention which topics.

Computed once per document from the terms in ``domain/topics.yaml`` and
stored in ``document_topics``, so the rankings never scan document bodies
at request time. ``documents.topics_version`` records the version of the
term list a document was tagged with; editing the list re-tags everything
on the next run.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from typing import Any

from asknews.db.connection import Connection, connect
from asknews.domain_config import Topic, topics, topics_version


def match_topics(text: str, candidates: Sequence[Topic]) -> list[str]:
    """Slugs of the topics with a term in ``text``; case-insensitive for Latin script."""
    haystack = text.casefold()
    return [
        topic.slug
        for topic in candidates
        if any(term.casefold() in haystack for terms in topic.terms.values() for term in terms)
    ]


def pending_documents(conn: Connection, version: str, *, limit: int) -> list[tuple[int, str]]:
    rows = conn.execute(
        """
        SELECT id, concat_ws(' ', title, body) AS text
          FROM documents
         WHERE topics_version IS DISTINCT FROM %s
         ORDER BY id
         LIMIT %s
        """,
        (version, limit),
    ).fetchall()
    return [(int(r["id"]), str(r["text"])) for r in rows]


def tag_documents(
    conn: Connection, candidates: Sequence[Topic], version: str, rows: Sequence[tuple[int, str]]
) -> None:
    ids = [doc_id for doc_id, _ in rows]
    matches = [(doc_id, slug) for doc_id, text in rows for slug in match_topics(text, candidates)]
    with conn.transaction():
        conn.execute("DELETE FROM document_topics WHERE document_id = ANY(%s)", (ids,))
        if matches:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO document_topics (document_id, topic) VALUES (%s, %s)", matches
                )
        conn.execute("UPDATE documents SET topics_version = %s WHERE id = ANY(%s)", (version, ids))


def tag_pending(
    conn: Connection,
    candidates: Sequence[Topic],
    *,
    batch_size: int = 200,
    limit: int | None = None,
) -> int:
    """Tag documents not yet tagged with this term list. Returns the count done."""
    version = topics_version(candidates)
    done = 0
    while limit is None or done < limit:
        size = batch_size if limit is None else min(batch_size, limit - done)
        batch = pending_documents(conn, version, limit=size)
        if not batch:
            break
        tag_documents(conn, candidates, version, batch)
        done += len(batch)
    return done


def run(args: argparse.Namespace) -> int:
    candidates = topics()
    started = time.monotonic()
    with connect(autocommit=True) as conn:
        done = tag_pending(conn, candidates, batch_size=args.batch, limit=args.limit or None)
    elapsed = time.monotonic() - started
    sys.stdout.write(
        f"{done} documents tagged with {len(candidates)} topics "
        f"(version {topics_version(candidates)}) in {elapsed:.1f}s\n"
    )
    return 0


def add_cli(subparsers: Any) -> None:
    parser = subparsers.add_parser("topics", help="tag documents with the topics they mention")
    parser.add_argument("--batch", type=int, default=200)
    parser.add_argument("--limit", type=int, default=0, help="stop after N documents")
    parser.set_defaults(func=run)
