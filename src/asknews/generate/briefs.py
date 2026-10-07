"""Daily and meeting briefs: a few bilingual points, each tied to the
documents it draws on."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, date, datetime, timedelta
from typing import Any

from asknews.answer.llm import LLM, Completion
from asknews.archive.meetings import Meeting, list_meetings
from asknews.budget import generation_allowed, record_usage
from asknews.db.connection import Connection, connect
from asknews.generate.common import clip, parse_json_object
from asknews.generate.summaries import llm_from_settings
from asknews.settings import get_settings

LOG = logging.getLogger(__name__)

MAX_DOCUMENTS = 25
DOC_CHARS = 1500

SYSTEM = """You write short briefs for an archive of official central bank and finance \
ministry publications. You receive numbered documents (title, publisher, date, excerpt). \
Reply with JSON only:
{"title": {"en": "...", "ja": "..."},
 "points": [{"en": "...", "ja": "...", "documents": [1, 3]}, ...]}
- Three to six points, each one or two sentences, each stating something a listed document \
says. Use only the documents; keep numbers, dates and names exact; no evaluation.
- "documents" lists the numbers of the documents the point draws on. Every point needs at least one.
- Give every text in both English (en) and Japanese (ja).
- The title is one line naming the day or the meeting and its main development.
Return nothing but the JSON object."""


def _document_block(index: int, row: dict[str, Any]) -> str:
    excerpt = row.get("summary") or clip(row.get("body"), DOC_CHARS)
    return (
        f"[{index}] {row['institution_name']} - {row['kind']}, {row['published_at']:%Y-%m-%d}: "
        f"{row['title']}\n{excerpt}"
    )


def _generate(
    conn: Connection, llm: LLM, heading: str, rows: list[dict[str, Any]], *, reference: str
) -> tuple[dict[str, Any], Completion] | None:
    user = (
        heading + "\n\n" + "\n\n".join(_document_block(i, r) for i, r in enumerate(rows, start=1))
    )
    completion = llm.complete(system=SYSTEM, user=user, max_tokens=2500)
    record_usage(conn, purpose="brief", completion=completion, reference=reference)
    data = parse_json_object(completion.text)
    points = data.get("points")
    title = data.get("title")
    if not isinstance(points, list) or not isinstance(title, dict):
        LOG.warning("brief reply was not in the expected shape")
        return None
    ids = [int(r["id"]) for r in rows]
    cleaned: list[dict[str, Any]] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        refs = [
            ids[int(n) - 1]
            for n in point.get("documents") or []
            if isinstance(n, int) and 1 <= n <= len(ids)
        ]
        if not refs or not point.get("en"):
            continue
        cleaned.append(
            {"en": str(point["en"]), "ja": str(point.get("ja") or point["en"]), "documents": refs}
        )
    if not cleaned:
        return None
    return {
        "title": {
            "en": str(title.get("en") or heading),
            "ja": str(title.get("ja") or title.get("en") or heading),
        },
        "points": cleaned,
    }, completion


def _store(
    conn: Connection,
    *,
    kind: str,
    brief_date: date,
    meeting_key: str | None,
    data: dict[str, Any],
    institutions: list[str],
    model: str,
) -> int:
    row = conn.execute(
        """
        INSERT INTO briefs (kind, brief_date, meeting_key, title, points, institutions, model)
        VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
        ON CONFLICT (kind, brief_date, meeting_key) DO UPDATE
           SET title = EXCLUDED.title, points = EXCLUDED.points,
               institutions = EXCLUDED.institutions, model = EXCLUDED.model, generated_at = now()
        RETURNING id
        """,
        (
            kind,
            brief_date,
            meeting_key,
            json.dumps(data["title"], ensure_ascii=False),
            json.dumps(data["points"], ensure_ascii=False),
            institutions,
            model,
        ),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def documents_on(conn: Connection, day: date) -> list[dict[str, Any]]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return conn.execute(
        """
        SELECT d.id, d.kind, d.title, d.summary, d.body, d.published_at,
               i.code AS institution, i.name AS institution_name
          FROM documents d JOIN institutions i ON i.id = d.institution_id
         WHERE d.published_at >= %s AND d.published_at < %s
         ORDER BY (i.code IN ('fed', 'boj')) DESC, d.published_at
         LIMIT %s
        """,
        (start, start + timedelta(days=1), MAX_DOCUMENTS),
    ).fetchall()


def daily_brief(conn: Connection, llm: LLM, day: date) -> int | None:
    rows = documents_on(conn, day)
    if not rows:
        return None
    generated = _generate(
        conn, llm, f"Daily brief for {day:%Y-%m-%d}.", rows, reference=f"daily:{day:%Y%m%d}"
    )
    if generated is None:
        return None
    data, completion = generated
    institutions = sorted({str(r["institution"]) for r in rows})
    return _store(
        conn,
        kind="daily",
        brief_date=day,
        meeting_key=None,
        data=data,
        institutions=institutions,
        model=completion.model,
    )


def meeting_documents(conn: Connection, meeting: Meeting) -> list[dict[str, Any]]:
    ids = [d.id for d in meeting.documents]
    rows = conn.execute(
        """
        SELECT d.id, d.kind, d.title, d.summary, d.body, d.published_at,
               i.code AS institution, i.name AS institution_name
          FROM documents d JOIN institutions i ON i.id = d.institution_id
         WHERE d.id = ANY(%s)
        """,
        (ids,),
    ).fetchall()
    order = {doc_id: n for n, doc_id in enumerate(ids)}
    return sorted(rows, key=lambda r: order[int(r["id"])])


def meeting_brief(conn: Connection, llm: LLM, meeting: Meeting) -> int | None:
    rows = meeting_documents(conn, meeting)
    if not rows:
        return None
    heading = (
        f"Brief for the {meeting.institution.upper()} policy meeting of {meeting.date:%Y-%m-%d}."
    )
    generated = _generate(conn, llm, heading, rows, reference=meeting.key)
    if generated is None:
        return None
    data, completion = generated
    return _store(
        conn,
        kind="meeting",
        brief_date=meeting.date,
        meeting_key=meeting.key,
        data=data,
        institutions=[meeting.institution],
        model=completion.model,
    )


def meetings_without_brief(conn: Connection, *, limit: int) -> list[Meeting]:
    existing = {
        r["meeting_key"]
        for r in conn.execute("SELECT meeting_key FROM briefs WHERE kind = 'meeting'").fetchall()
    }
    return [m for m in list_meetings(conn, limit=400) if m.key not in existing][:limit]


BUDGET_NOTE = "stopped at the daily budget's generation share\n"


def run_daily(args: argparse.Namespace) -> int:
    llm = llm_from_settings()
    budget = get_settings().daily_budget_usd
    today = datetime.now(UTC).date()
    days = (
        [date.fromisoformat(args.date)]
        if args.date
        else [today - timedelta(days=n) for n in range(1, args.days + 1)]
    )
    with connect(autocommit=True) as conn:
        for day in days:
            exists = conn.execute(
                "SELECT 1 FROM briefs WHERE kind = 'daily' AND brief_date = %s", (day,)
            ).fetchone()
            if exists and not args.refresh:
                continue
            if not generation_allowed(conn, budget):
                sys.stdout.write(BUDGET_NOTE)
                break
            brief_id = daily_brief(conn, llm, day)
            sys.stdout.write(f"{day}: {'brief ' + str(brief_id) if brief_id else 'no documents'}\n")
    return 0


def run_meetings(args: argparse.Namespace) -> int:
    llm = llm_from_settings()
    budget = get_settings().daily_budget_usd
    with connect(autocommit=True) as conn:
        for meeting in meetings_without_brief(conn, limit=args.limit):
            if not generation_allowed(conn, budget):
                sys.stdout.write(BUDGET_NOTE)
                break
            brief_id = meeting_brief(conn, llm, meeting)
            sys.stdout.write(
                f"{meeting.key}: {'brief ' + str(brief_id) if brief_id else 'skipped'}\n"
            )
    return 0


def add_cli(subparsers: Any) -> None:
    parser = subparsers.add_parser("briefs", help="generate daily and meeting briefs")
    kinds = parser.add_subparsers(dest="brief_kind", required=True)
    daily = kinds.add_parser("daily", help="one brief per calendar day with releases")
    daily.add_argument("--date", help="a single day (YYYY-MM-DD)")
    daily.add_argument("--days", type=int, default=7, help="how many past days to cover")
    daily.add_argument("--refresh", action="store_true", help="regenerate existing briefs")
    daily.set_defaults(func=run_daily)
    meetings = kinds.add_parser("meetings", help="one brief per policy meeting")
    meetings.add_argument("--limit", type=int, default=5)
    meetings.set_defaults(func=run_meetings)
