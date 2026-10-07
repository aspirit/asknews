"""Writing documents, segments, media and speakers.

All writes are idempotent. A document is identified by publisher, kind and
external id; segments are replaced wholesale, but only when their content
changed, so embeddings survive a no-op re-ingest.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from asknews.db.connection import Connection


@dataclass(frozen=True, slots=True)
class Segment:
    position: int
    text: str
    speaker: str | None = None
    start_sec: float | None = None
    end_sec: float | None = None


@dataclass(frozen=True, slots=True)
class Document:
    institution: str
    kind: str
    lang: str
    external_id: str
    title: str
    published_at: datetime
    source_url: str
    body: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    speaker_id: int | None = None


@dataclass(frozen=True, slots=True)
class Media:
    provider: str
    provider_id: str
    duration_sec: int | None = None


def clean_text(value: str | None) -> str | None:
    """PostgreSQL text cannot hold NUL bytes; some PDF and HTML sources leak them."""
    return value.replace(chr(0), "") if value is not None else None


def institution_id(conn: Connection, code: str) -> int:
    row = conn.execute("SELECT id FROM institutions WHERE code = %s", (code,)).fetchone()
    if row is None:
        raise LookupError(f"unknown institution code: {code}")
    return int(row["id"])


def upsert_speaker(
    conn: Connection,
    *,
    institution: str,
    slug: str,
    name: str,
    role: str | None,
    name_ja: str | None = None,
) -> int:
    row = conn.execute(
        """
        INSERT INTO speakers (institution_id, slug, name, role, name_ja)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (slug) DO UPDATE
           SET name = EXCLUDED.name, role = EXCLUDED.role,
               name_ja = coalesce(EXCLUDED.name_ja, speakers.name_ja)
        RETURNING id
        """,
        (institution_id(conn, institution), slug, name, role, name_ja),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def upsert_document(conn: Connection, doc: Document) -> int:
    """Insert or update a document and return its id."""
    row = conn.execute(
        """
        INSERT INTO documents
            (institution_id, kind, lang, external_id, title, published_at,
             source_url, body, meta, speaker_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (institution_id, kind, external_id) DO UPDATE
           SET title = EXCLUDED.title,
               lang = EXCLUDED.lang,
               published_at = EXCLUDED.published_at,
               source_url = EXCLUDED.source_url,
               body = EXCLUDED.body,
               meta = EXCLUDED.meta,
               speaker_id = EXCLUDED.speaker_id,
               updated_at = now()
        RETURNING id
        """,
        (
            institution_id(conn, doc.institution),
            doc.kind,
            doc.lang,
            doc.external_id,
            clean_text(doc.title),
            doc.published_at,
            doc.source_url,
            clean_text(doc.body),
            json.dumps(doc.meta, ensure_ascii=False),
            doc.speaker_id,
        ),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def segments_hash(segments: list[Segment]) -> str:
    digest = hashlib.sha256()
    for seg in segments:
        digest.update(f"{seg.position}\t{seg.speaker or ''}\t{seg.text}\n".encode())
    return digest.hexdigest()


def replace_segments(conn: Connection, document_id: int, segments: list[Segment]) -> bool:
    """Write the segments of a document. Returns False when nothing changed."""
    new_hash = segments_hash(segments)
    row = conn.execute(
        "SELECT content_hash FROM documents WHERE id = %s", (document_id,)
    ).fetchone()
    if row is not None and row["content_hash"] == new_hash:
        return False
    with conn.transaction():
        conn.execute("DELETE FROM segments WHERE document_id = %s", (document_id,))
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO segments (document_id, position, text, speaker, start_sec, end_sec)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                [
                    (document_id, s.position, clean_text(s.text), s.speaker, s.start_sec, s.end_sec)
                    for s in segments
                ],
            )
        conn.execute(
            "UPDATE documents SET content_hash = %s WHERE id = %s", (new_hash, document_id)
        )
    return True


def upsert_media(conn: Connection, document_id: int, media: Media) -> None:
    conn.execute(
        """
        INSERT INTO media (document_id, provider, provider_id, duration_sec)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (document_id) DO UPDATE
           SET provider = EXCLUDED.provider,
               provider_id = EXCLUDED.provider_id,
               duration_sec = EXCLUDED.duration_sec
        """,
        (document_id, media.provider, media.provider_id, media.duration_sec),
    )
