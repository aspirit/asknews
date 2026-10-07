"""Rankings computed from published material: speakers, publishers, topics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from asknews.db.connection import Connection
from asknews.domain_config import Topic, topics


@dataclass(frozen=True, slots=True)
class SpeakerRank:
    slug: str
    name: str
    name_ja: str | None
    role: str | None
    institution: str
    speeches: int
    press_conferences: int
    minutes_spoken: int

    def name_in(self, lang: str) -> str:
        return self.name_ja if lang == "ja" and self.name_ja else self.name


@dataclass(frozen=True, slots=True)
class InstitutionRank:
    code: str
    name: str
    total: int
    by_kind: dict[str, int]


@dataclass(frozen=True, slots=True)
class TopicRank:
    topic: Topic
    documents: int
    previous: int

    @property
    def change(self) -> int:
        return self.documents - self.previous


def speakers(conn: Connection, *, days: int | None = 90, limit: int = 12) -> list[SpeakerRank]:
    since = datetime.now(UTC) - timedelta(days=days) if days else datetime(1900, 1, 1, tzinfo=UTC)
    rows = conn.execute(
        """
        SELECT sp.slug, sp.name, sp.name_ja, sp.role, i.code AS institution,
               count(*) FILTER (WHERE d.kind = 'speech') AS speeches,
               count(*) FILTER (WHERE d.kind = 'press_conference') AS press_conferences,
               coalesce(sum(
                   CASE WHEN d.kind = 'press_conference'
                        THEN coalesce(m.duration_sec,
                                      (SELECT max(end_sec) FROM segments s
                                        WHERE s.document_id = d.id))
                        ELSE 0 END), 0) AS seconds
          FROM documents d
          JOIN speakers sp ON sp.id = d.speaker_id
          JOIN institutions i ON i.id = sp.institution_id
          LEFT JOIN media m ON m.document_id = d.id
         WHERE d.published_at >= %s
         GROUP BY sp.slug, sp.name, sp.name_ja, sp.role, i.code
         ORDER BY (count(*) FILTER (WHERE d.kind = 'speech'))
                  + 2 * (count(*) FILTER (WHERE d.kind = 'press_conference')) DESC, sp.name
         LIMIT %s
        """,
        (since, limit),
    ).fetchall()
    return [
        SpeakerRank(
            r["slug"],
            r["name"],
            r["name_ja"],
            r["role"],
            r["institution"],
            int(r["speeches"]),
            int(r["press_conferences"]),
            int(float(r["seconds"]) // 60),
        )
        for r in rows
    ]


def institutions(conn: Connection, *, days: int = 30) -> list[InstitutionRank]:
    rows = conn.execute(
        """
        SELECT i.code, i.name, d.kind, count(*) AS n
          FROM documents d JOIN institutions i ON i.id = d.institution_id
         WHERE d.published_at >= %s
         GROUP BY i.code, i.name, d.kind
        """,
        (datetime.now(UTC) - timedelta(days=days),),
    ).fetchall()
    grouped: dict[str, dict[str, Any]] = {}
    for r in rows:
        entry = grouped.setdefault(r["code"], {"name": r["name"], "by_kind": {}})
        entry["by_kind"][r["kind"]] = int(r["n"])
    ranks = [
        InstitutionRank(code, v["name"], sum(v["by_kind"].values()), v["by_kind"])
        for code, v in grouped.items()
    ]
    return sorted(ranks, key=lambda r: (-r.total, r.code))


def topic_ranks(conn: Connection, *, days: int = 90) -> list[TopicRank]:
    """Documents per topic in the period and in the period before it.

    Reads ``document_topics``, which ``asknews topics`` fills from the term
    list, so this is a group-by rather than a scan of document bodies.
    """
    now = datetime.now(UTC)
    start = now - timedelta(days=days)
    previous_start = start - timedelta(days=days)
    rows = conn.execute(
        """
        SELECT t.topic,
               count(*) FILTER (WHERE d.published_at >= %s) AS in_period,
               count(*) FILTER (WHERE d.published_at < %s) AS before_period
          FROM document_topics t JOIN documents d ON d.id = t.document_id
         WHERE d.published_at >= %s AND d.published_at < %s
         GROUP BY t.topic
        """,
        (start, start, previous_start, now),
    ).fetchall()
    counts = {str(r["topic"]): (int(r["in_period"]), int(r["before_period"])) for r in rows}
    ranks = [
        TopicRank(topic, *counts.get(topic.slug, (0, 0)))
        for topic in topics()
        if any(topic.terms.values())
    ]
    return sorted(ranks, key=lambda r: (-r.documents, r.topic.slug))
