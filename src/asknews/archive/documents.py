"""Document lists and detail."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from asknews.db.connection import Connection

KINDS = ("press_conference", "statement", "minutes", "speech", "report", "release")

_LIST_SQL = """
SELECT d.id, d.kind, d.lang, d.title, d.published_at, d.source_url, d.summary,
       i.code AS institution, i.name AS institution_name,
       sp.slug AS speaker_slug, sp.name AS speaker, sp.role AS speaker_role,
       sp.name_ja AS speaker_ja,
       m.provider AS media_provider, m.provider_id AS media_id, m.duration_sec,
       d.meta->>'timestamped' AS timestamped,
       t_en.title AS title_en, t_en.summary AS summary_en,
       t_ja.title AS title_ja, t_ja.summary AS summary_ja
  FROM documents d
  JOIN institutions i ON i.id = d.institution_id
  LEFT JOIN speakers sp ON sp.id = d.speaker_id
  LEFT JOIN media m ON m.document_id = d.id
  LEFT JOIN document_translations t_en ON t_en.document_id = d.id AND t_en.lang = 'en'
  LEFT JOIN document_translations t_ja ON t_ja.document_id = d.id AND t_ja.lang = 'ja'
"""


@dataclass(frozen=True, slots=True)
class DocumentSummary:
    id: int
    institution: str
    institution_name: str
    kind: str
    lang: str
    title: str
    published_at: datetime
    source_url: str
    summary: str | None
    speaker_slug: str | None
    speaker: str | None
    speaker_role: str | None
    speaker_ja: str | None
    media_provider: str | None
    media_id: str | None
    duration_sec: int | None
    timestamped: bool
    translations: dict[str, dict[str, str | None]] = field(default_factory=dict)

    def title_in(self, lang: str) -> tuple[str, bool]:
        """``(title, translated)`` for the interface language."""
        if lang != self.lang and self.translations.get(lang, {}).get("title"):
            return str(self.translations[lang]["title"]), True
        return self.title, False

    def speaker_in(self, lang: str) -> str | None:
        """The principal speaker's name in the interface language when known."""
        return self.speaker_ja if lang == "ja" and self.speaker_ja else self.speaker

    def summary_in(self, lang: str) -> str | None:
        if lang != self.lang and self.translations.get(lang, {}).get("summary"):
            return self.translations[lang]["summary"]
        return self.summary


def _row_to_summary(row: dict[str, Any]) -> DocumentSummary:
    translations: dict[str, dict[str, str | None]] = {}
    for lang in ("en", "ja"):
        if row.get(f"title_{lang}"):
            translations[lang] = {
                "title": row[f"title_{lang}"],
                "summary": row.get(f"summary_{lang}"),
            }
    return DocumentSummary(
        id=int(row["id"]),
        institution=row["institution"],
        institution_name=row["institution_name"],
        kind=row["kind"],
        lang=row["lang"],
        title=row["title"],
        published_at=row["published_at"],
        source_url=row["source_url"],
        summary=row["summary"],
        speaker_slug=row["speaker_slug"],
        speaker=row["speaker"],
        speaker_role=row["speaker_role"],
        speaker_ja=row["speaker_ja"],
        media_provider=row["media_provider"],
        media_id=row["media_id"],
        duration_sec=row["duration_sec"],
        timestamped=row["timestamped"] == "true",
        translations=translations,
    )


def latest(
    conn: Connection,
    *,
    institution: str | None = None,
    kind: str | None = None,
    with_media: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> list[DocumentSummary]:
    conditions = ["TRUE"]
    params: list[Any] = []
    if institution:
        conditions.append("i.code = %s")
        params.append(institution)
    if kind:
        conditions.append("d.kind = %s")
        params.append(kind)
    if with_media:
        conditions.append("m.provider IS NOT NULL")
    rows = conn.execute(
        _LIST_SQL + f" WHERE {' AND '.join(conditions)} ORDER BY d.published_at DESC, d.id DESC "
        "LIMIT %s OFFSET %s",
        [*params, limit, offset],
    ).fetchall()
    return [_row_to_summary(r) for r in rows]


# Ordering for the home page: what matters most among recent releases, from
# document attributes alone (kind, publisher, video, who spoke, age).
_PROMINENCE_SQL = """
    CASE d.kind WHEN 'statement' THEN 3 WHEN 'press_conference' THEN 3 WHEN 'minutes' THEN 2
                WHEN 'report' THEN 1.5 WHEN 'speech' THEN 1 ELSE 0.5 END
    + CASE WHEN i.code IN ('fed', 'boj') THEN 2 ELSE 0 END
    + CASE WHEN m.provider IS NOT NULL THEN 1 ELSE 0 END
    + CASE WHEN sp.role IN ('Chair', 'Chairman', 'Governor') THEN 1 ELSE 0 END
    - EXTRACT(EPOCH FROM (now() - d.published_at)) / 86400.0 / 30.0
"""


def prominent(conn: Connection, *, limit: int = 10) -> list[DocumentSummary]:
    """Releases ranked by prominence, where age counts against a document but
    does not exclude it: a quiet month still fills the list."""
    rows = conn.execute(
        _LIST_SQL + f" ORDER BY ({_PROMINENCE_SQL}) DESC, d.published_at DESC LIMIT %s",
        [limit],
    ).fetchall()
    return [_row_to_summary(r) for r in rows]


def by_ids(conn: Connection, ids: list[int]) -> dict[int, DocumentSummary]:
    if not ids:
        return {}
    rows = conn.execute(_LIST_SQL + " WHERE d.id = ANY(%s)", (ids,)).fetchall()
    return {int(r["id"]): _row_to_summary(r) for r in rows}


@dataclass(frozen=True, slots=True)
class SegmentView:
    id: int
    position: int
    text: str
    speaker: str | None
    start_sec: float | None
    end_sec: float | None


@dataclass(frozen=True, slots=True)
class SpeakerBlock:
    """Consecutive segments by one speaker: a question, or a whole answer."""

    speaker: str | None
    first: int  # position of the first segment, which carries the anchor
    anchors: list[int]  # positions of the other segments, kept as link targets
    texts: list[str]


def speaker_blocks(segments: Sequence[SegmentView]) -> list[SpeakerBlock]:
    blocks: list[SpeakerBlock] = []
    for segment in segments:
        if blocks and blocks[-1].speaker == segment.speaker:
            blocks[-1].anchors.append(segment.position)
            blocks[-1].texts.append(segment.text)
        else:
            blocks.append(SpeakerBlock(segment.speaker, segment.position, [], [segment.text]))
    return blocks


@dataclass(frozen=True, slots=True)
class DocumentDetail:
    document: DocumentSummary
    segments: list[SegmentView]
    meta: dict[str, Any]


def detail(conn: Connection, document_id: int) -> DocumentDetail | None:
    summary = by_ids(conn, [document_id]).get(document_id)
    if summary is None:
        return None
    meta_row = conn.execute("SELECT meta FROM documents WHERE id = %s", (document_id,)).fetchone()
    rows = conn.execute(
        "SELECT id, position, text, speaker, start_sec, end_sec FROM segments "
        "WHERE document_id = %s ORDER BY position",
        (document_id,),
    ).fetchall()
    segments = [
        SegmentView(
            int(r["id"]), int(r["position"]), r["text"], r["speaker"], r["start_sec"], r["end_sec"]
        )
        for r in rows
    ]
    return DocumentDetail(summary, segments, dict(meta_row["meta"]) if meta_row else {})


def counts(conn: Connection) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT count(*) AS documents,
               count(*) FILTER (WHERE kind = 'press_conference') AS press_conferences,
               (SELECT count(*) FROM media) AS videos,
               (SELECT count(*) FROM speakers) AS speakers
          FROM documents
        """
    ).fetchone()
    return {k: int(v) for k, v in (row or {}).items()}
