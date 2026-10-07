"""Stored briefs: reading side."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from asknews.db.connection import Connection


@dataclass(frozen=True, slots=True)
class Brief:
    id: int
    kind: str
    brief_date: date
    meeting_key: str | None
    title: dict[str, str]
    points: list[dict[str, Any]]
    institutions: list[str]
    model: str
    generated_at: datetime

    def title_in(self, lang: str) -> str:
        return self.title.get(lang) or self.title.get("en") or next(iter(self.title.values()), "")

    def points_in(self, lang: str) -> list[dict[str, Any]]:
        out = []
        for point in self.points:
            text = point.get(lang) or point.get("en") or ""
            out.append({"text": text, "documents": list(point.get("documents") or [])})
        return out


def _row(r: dict[str, Any]) -> Brief:
    return Brief(
        int(r["id"]),
        r["kind"],
        r["brief_date"],
        r["meeting_key"],
        dict(r["title"]),
        list(r["points"]),
        list(r["institutions"]),
        r["model"],
        r["generated_at"],
    )


def list_briefs(conn: Connection, *, kind: str | None = None, limit: int = 30) -> list[Brief]:
    condition = "WHERE kind = %s" if kind else ""
    params: list[Any] = [kind] if kind else []
    rows = conn.execute(
        f"SELECT * FROM briefs {condition} ORDER BY brief_date DESC, id DESC LIMIT %s",
        [*params, limit],
    ).fetchall()
    return [_row(r) for r in rows]


def get_brief(conn: Connection, brief_id: int) -> Brief | None:
    row = conn.execute("SELECT * FROM briefs WHERE id = %s", (brief_id,)).fetchone()
    return _row(row) if row else None


def brief_for_meeting(conn: Connection, meeting_key: str) -> Brief | None:
    row = conn.execute(
        "SELECT * FROM briefs WHERE kind = 'meeting' AND meeting_key = %s ORDER BY id DESC LIMIT 1",
        (meeting_key,),
    ).fetchone()
    return _row(row) if row else None
