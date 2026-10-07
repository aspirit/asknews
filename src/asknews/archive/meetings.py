"""Policy meetings as bundles of the documents each one produced.

A meeting is not stored; it is read off the documents by date. The Federal
Reserve's statement, press conference and minutes share the meeting's end
date in their identifiers. The Bank of Japan's statement, regular press
conference and outlook report share a publication date, while its minutes
and Summary of Opinions name the meeting dates in their metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from asknews.archive.documents import DocumentSummary, by_ids
from asknews.db.connection import Connection

JST = timezone(timedelta(hours=9))
# Title prefix of the Governor's regular post-meeting press conference.
_REGULAR = "".join(chr(c) for c in (0x7DCF, 0x88C1, 0x5B9A, 0x4F8B))
_FED_RANGE = re.compile(
    r"target range for the federal funds rate(?: by [^,.]*? percentage points?)? (?:at|to) "
    r"(\d[\d\-/]* to \d[\d\-/]* percent)",
    re.I,
)
_FED_HOLD = re.compile(
    r"maintain the target range for the federal funds rate at (\d[\d\-/]* to \d[\d\-/]* percent)",
    re.I,
)
_TEIDO = chr(0x7A0B) + chr(0x5EA6)  # "approximately", written after a rate
_BOJ_RATE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*" + _TEIDO)
_GATSU, _NICHI, _TOUTEN = chr(0x6708), chr(0x65E5), chr(0x3001)  # month, day, comma
_JA_MEETING = re.compile(
    r"(\d{1,2})" + _GATSU + r"\s*(\d{1,2})(?:" + _TOUTEN + r"\s*(\d{1,2}))?\s*" + _NICHI
)


@dataclass(slots=True)
class Meeting:
    institution: str
    date: date
    statement: DocumentSummary | None = None
    press_conference: DocumentSummary | None = None
    minutes: DocumentSummary | None = None
    opinions: DocumentSummary | None = None
    outlook: DocumentSummary | None = None
    decision: str | None = None
    documents: list[DocumentSummary] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.institution}:{self.date:%Y%m%d}"


def meeting_date_from_text(meeting_text: str, published: date) -> date | None:
    """The last meeting day named in Japanese text such as "month 7, days 30 and 31";
    a December meeting published in January belongs to the previous year."""
    match = _JA_MEETING.search(meeting_text)
    if match is None:
        return None
    month = int(match.group(1))
    day = int(match.group(3) or match.group(2))
    year = published.year - 1 if month > published.month else published.year
    try:
        return date(year, month, day)
    except ValueError:
        return None


def fed_decision(statement_body: str | None) -> str | None:
    if not statement_body:
        return None
    match = _FED_RANGE.search(statement_body) or _FED_HOLD.search(statement_body)
    return match.group(1) if match else None


def boj_decision(statement_body: str | None) -> str | None:
    if not statement_body:
        return None
    match = _BOJ_RATE.search(statement_body)
    return f"{match.group(1)}%" if match else None


def list_meetings(conn: Connection, *, limit: int = 40) -> list[Meeting]:
    rows = conn.execute(
        """
        SELECT d.id, d.kind, d.external_id, d.published_at, d.title, d.meta, i.code AS institution,
               left(d.body, 4000) AS body_head
          FROM documents d JOIN institutions i ON i.id = d.institution_id
         WHERE i.code IN ('fed', 'boj')
           AND (
                (i.code = 'fed' AND d.kind IN ('statement', 'press_conference', 'minutes'))
             OR (i.code = 'boj' AND (
                    (d.kind = 'statement')
                 OR (d.kind = 'press_conference' AND d.title LIKE %s)
                 OR (d.kind IN ('minutes', 'report'))))
           )
         ORDER BY d.published_at DESC
        """,
        ("%" + _REGULAR + "%",),
    ).fetchall()
    meetings: dict[str, Meeting] = {}

    def slot(institution: str, when: date) -> Meeting:
        key = f"{institution}:{when:%Y%m%d}"
        if key not in meetings:
            meetings[key] = Meeting(institution, when)
        return meetings[key]

    bodies: dict[int, str | None] = {}
    for row in rows:
        institution = row["institution"]
        kind = row["kind"]
        published: datetime = row["published_at"]
        meta = row["meta"] or {}
        if institution == "fed":
            try:
                when = datetime.strptime(row["external_id"], "%Y%m%d").date()
            except ValueError:
                continue
        else:
            local = published.astimezone(JST).date()
            if kind in ("minutes", "report") and meta.get("subkind") != "outlook":
                parsed = meeting_date_from_text(str(meta.get("meeting", "")), local)
                if parsed is None:
                    continue
                when = parsed
            else:
                when = local
        meeting = slot(institution, when)
        bodies[int(row["id"])] = row["body_head"]
        if kind == "statement":
            meeting.statement = meeting.statement or _placeholder(row)
        elif kind == "press_conference":
            meeting.press_conference = meeting.press_conference or _placeholder(row)
        elif kind == "minutes":
            meeting.minutes = meeting.minutes or _placeholder(row)
        elif meta.get("subkind") == "opinions":
            meeting.opinions = meeting.opinions or _placeholder(row)
        elif meta.get("subkind") == "outlook":
            meeting.outlook = meeting.outlook or _placeholder(row)

    # Only meetings that actually produced a statement count; stray minutes
    # without a statement in the archive would otherwise appear as meetings.
    selected = sorted(
        (m for m in meetings.values() if m.statement is not None),
        key=lambda m: m.date,
        reverse=True,
    )[:limit]
    ids = [
        doc.id
        for m in selected
        for doc in (m.statement, m.press_conference, m.minutes, m.opinions, m.outlook)
        if doc is not None
    ]
    full = by_ids(conn, ids)
    for meeting in selected:
        for attr in ("statement", "press_conference", "minutes", "opinions", "outlook"):
            doc = getattr(meeting, attr)
            if doc is not None and doc.id in full:
                setattr(meeting, attr, full[doc.id])
        meeting.documents = [
            getattr(meeting, attr)
            for attr in ("statement", "press_conference", "minutes", "opinions", "outlook")
            if getattr(meeting, attr) is not None
        ]
        body = bodies.get(meeting.statement.id) if meeting.statement else None
        meeting.decision = (
            fed_decision(body) if meeting.institution == "fed" else boj_decision(body)
        )
    return selected


def _placeholder(row: dict[str, object]) -> DocumentSummary:
    """A minimal summary carrying the id; replaced by the full row later."""
    return DocumentSummary(
        id=int(row["id"]),  # type: ignore[call-overload]
        institution=str(row["institution"]),
        institution_name="",
        kind=str(row["kind"]),
        lang="",
        title=str(row["title"]),
        published_at=row["published_at"],  # type: ignore[arg-type]
        source_url="",
        summary=None,
        speaker_slug=None,
        speaker=None,
        speaker_role=None,
        media_provider=None,
        media_id=None,
        duration_sec=None,
        timestamped=False,
    )
