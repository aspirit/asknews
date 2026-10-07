"""Rankings computed from published material: speakers, publishers, topics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from asknews.db.connection import Connection
from asknews.settings import get_settings


@dataclass(frozen=True, slots=True)
class SpeakerRank:
    slug: str
    name: str
    role: str | None
    institution: str
    speeches: int
    press_conferences: int
    minutes_spoken: int


@dataclass(frozen=True, slots=True)
class InstitutionRank:
    code: str
    name: str
    total: int
    by_kind: dict[str, int]


@dataclass(frozen=True, slots=True)
class Topic:
    slug: str
    label: dict[str, str]
    terms: dict[str, list[str]]


@dataclass(frozen=True, slots=True)
class TopicRank:
    topic: Topic
    documents: int
    previous: int

    @property
    def change(self) -> int:
        return self.documents - self.previous


def load_topics(path: Path) -> list[Topic]:
    raw = yaml.safe_load(path.read_text("utf-8")) or {}
    return [
        Topic(
            slug=str(t["slug"]),
            label={str(k): str(v) for k, v in (t.get("label") or {}).items()},
            terms={str(k): [str(x) for x in v or []] for k, v in (t.get("terms") or {}).items()},
        )
        for t in raw.get("topics") or []
    ]


@lru_cache(maxsize=1)
def topics() -> list[Topic]:
    return load_topics(get_settings().domain_dir / "topics.yaml")


def speakers(conn: Connection, *, days: int | None = 90, limit: int = 12) -> list[SpeakerRank]:
    since = datetime.now(UTC) - timedelta(days=days) if days else datetime(1900, 1, 1, tzinfo=UTC)
    rows = conn.execute(
        """
        SELECT sp.slug, sp.name, sp.role, i.code AS institution,
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
         GROUP BY sp.slug, sp.name, sp.role, i.code
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
    """Documents per topic in the period and in the period before it, in one scan."""
    now = datetime.now(UTC)
    start = now - timedelta(days=days)
    previous_start = start - timedelta(days=days)
    selected = [t for t in topics() if any(t.terms.values())]
    if not selected:
        return []
    columns: list[str] = []
    params: list[Any] = []
    for index, topic in enumerate(selected):
        patterns = [f"%{term}%" for lang_terms in topic.terms.values() for term in lang_terms]
        match = "(d.title ILIKE ANY(%s) OR d.body ILIKE ANY(%s))"
        columns.append(
            f"count(*) FILTER (WHERE d.published_at >= %s AND {match}) AS current_{index}, "
            f"count(*) FILTER (WHERE d.published_at < %s AND {match}) AS previous_{index}"
        )
        params += [start, patterns, patterns, start, patterns, patterns]
    row = conn.execute(
        f"SELECT {', '.join(columns)} FROM documents d "
        "WHERE d.published_at >= %s AND d.published_at < %s",
        [*params, previous_start, now],
    ).fetchone()
    ranks = [
        TopicRank(topic, int(row[f"current_{i}"]), int(row[f"previous_{i}"]))
        if row
        else TopicRank(topic, 0, 0)
        for i, topic in enumerate(selected)
    ]
    return sorted(ranks, key=lambda r: (-r.documents, r.topic.slug))
