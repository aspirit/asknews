"""Policy meetings as bundles of the documents each one produced.

A meeting is not stored; it is read off the documents by date. The Federal
Reserve's statement, press conference, minutes and projection materials
share the meeting's end date in their identifiers. The Bank of Japan's
statement, regular press conference and outlook report share a publication
date, while its minutes and Summary of Opinions name the meeting dates in
their metadata.

Each meeting also knows which documents it should have: a press conference
at every Federal Reserve meeting since 2019, minutes three weeks later,
projections at four meetings a year, and so on. A document that is not in
the archive is then "scheduled" (its release day has not come), "missing"
(it should be here) or "none" (never published for that meeting).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction

from asknews.archive.documents import DocumentSummary, by_ids
from asknews.db.connection import Connection
from asknews.domain_config import meeting_statement_titles

JST = timezone(timedelta(hours=9))
ROLES = {
    "fed": ("statement", "press_conference", "minutes", "outlook"),
    "boj": ("statement", "press_conference", "minutes", "opinions", "outlook"),
}
EN_DASH = chr(0x2013)

# Title prefix of the Governor's regular post-meeting press conference.
_REGULAR = "".join(chr(c) for c in (0x7DCF, 0x88C1, 0x5B9A, 0x4F8B))
_FED = re.compile(
    r"\b(raise|lower|maintain|keep|increase|decrease)\b the target range for the federal funds "
    r"rate(?: by (\d[\d\-/]*) percentage points?,?)? (?:at|to) "
    r"(\d[\d\-/]*) to (\d[\d\-/]*) percent",
    re.I,
)
# A voting sentence ends at a period that does not belong to an initial.
_FED_FOR = re.compile(
    r"Voting for (?:this|the monetary policy) action were (.+?)(?<![A-Z])\.(?:\s|$)", re.S
)
_FED_AGAINST = re.compile(
    r"Voting against (?:this|the monetary policy) action (?:was|were) (.+?)(?<![A-Z])\.(?:\s|$)",
    re.S,
)
_NAME = re.compile(r"[A-Z][A-Za-z.'\-]+(?: [A-Z][A-Za-z.'\-]+)+")
_TITLES = {"Vice Chair", "Chair Pro Tempore"}  # listed after a name, never a name
_TEIDO = chr(0x7A0B) + chr(0x5EA6)  # "approximately", written after a rate
_BOJ_RATE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*" + _TEIDO)
_MINUS_RATE = "".join(chr(c) for c in (0x306E, 0x30DE, 0x30A4, 0x30CA, 0x30B9, 0x91D1, 0x5229))
_BOJ_NEGATIVE = re.compile("[" + chr(0x25B2) + r"\-](\d+(?:\.\d+)?)\s*%" + _MINUS_RATE)
_SANSEI, _HANTAI = (
    "".join(chr(c) for c in (0x8CDB, 0x6210)),
    "".join(chr(c) for c in (0x53CD, 0x5BFE)),
)
_BOJ_VOTES = re.compile(chr(0xFF08) + _SANSEI + r"(\d+)" + _HANTAI + r"(\d+)" + chr(0xFF09))
_BOJ_AGAINST = re.compile(_HANTAI + chr(0xFF1A) + r"(.*?)" + chr(0x3002))
_UNANIMOUS = "".join(chr(c) for c in (0x5168, 0x54E1, 0x4E00, 0x81F4))
_IIN = chr(0x59D4) + chr(0x54E1)  # the title after a board member's name
_TOUTEN = chr(0x3001)
_GATSU, _NICHI = chr(0x6708), chr(0x65E5)
_JA_MEETING = re.compile(
    r"(\d{1,2})" + _GATSU + r"\s*(\d{1,2})(?:" + _TOUTEN + r"\s*(\d{1,2}))?\s*" + _NICHI
)


@dataclass(frozen=True, slots=True)
class Decision:
    """The rate decision read from a statement, with the vote when given."""

    text: str  # the rate or range as the statement words it
    low: float | None
    high: float | None
    change_bp: int | None  # signed basis points against the previous decision
    unanimous: bool | None
    dissenters: tuple[str, ...]
    votes: str | None  # "7-2" when counts are known

    @property
    def action(self) -> str:
        if self.change_bp is None:
            return "set"
        return "raise" if self.change_bp > 0 else "lower" if self.change_bp < 0 else "hold"

    @property
    def range_text(self) -> str:
        if self.low is None:
            return self.text
        if self.high is None or self.high == self.low:
            return f"{self.low:.2f}%"
        return f"{self.low:.2f}{EN_DASH}{self.high:.2f}%"


@dataclass(frozen=True, slots=True)
class Part:
    role: str
    document: DocumentSummary | None
    status: str  # present | scheduled | missing | none


@dataclass(slots=True)
class Meeting:
    institution: str
    date: date
    statement: DocumentSummary | None = None
    press_conference: DocumentSummary | None = None
    minutes: DocumentSummary | None = None
    opinions: DocumentSummary | None = None
    outlook: DocumentSummary | None = None
    decision: Decision | None = None
    parts: list[Part] = field(default_factory=list)
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


def _fed_number(text: str) -> float:
    """``3-3/4`` -> 3.75, ``1/4`` -> 0.25, ``4`` -> 4.0."""
    whole, _, fraction = text.partition("-")
    value = Fraction(whole) + (Fraction(fraction) if fraction else 0)
    return float(value)


def _names(text: str) -> tuple[str, ...]:
    """Names in a voting sentence, skipping the "who preferred ..." clauses."""
    found: list[str] = []
    for clause in re.split(r";\s*|,? and |, ", text):
        match = _NAME.match(re.sub(r"^and\s+", "", clause.strip()))
        if match and match.group(0) not in found and match.group(0) not in _TITLES:
            found.append(match.group(0))
    return tuple(found)


def fed_decision(statement_body: str | None) -> Decision | None:
    if not statement_body:
        return None
    match = _FED.search(statement_body)
    if match is None:
        return None
    verb, step, low, high = match.groups()
    change = 0
    if verb.lower() in ("raise", "increase", "lower", "decrease"):
        change = round(_fed_number(step) * 100) if step else 0
        change = change if verb.lower() in ("raise", "increase") else -change
    against = _FED_AGAINST.search(statement_body)
    for_ = _FED_FOR.search(statement_body)
    dissenters = _names(against.group(1)) if against else ()
    votes = None
    if for_ and (dissenters or against is None):
        votes = f"{len(_names(for_.group(1)))}-{len(dissenters)}"
    return Decision(
        text=f"{low} to {high} percent",
        low=_fed_number(low),
        high=_fed_number(high),
        change_bp=change,
        unanimous=(against is None) if for_ else None,
        dissenters=dissenters,
        votes=votes,
    )


def boj_decision(
    statement_body: str | None, *, previous_rate: float | None = None
) -> Decision | None:
    """The short-term policy rate as the statement sets it; the change is
    measured against the previous meeting's rate when that is known."""
    if not statement_body:
        return None
    rate: float | None = None
    text = ""
    match = _BOJ_RATE.search(statement_body)
    if match:
        rate, text = float(match.group(1)), f"{match.group(1)}%"
    else:
        negative = _BOJ_NEGATIVE.search(statement_body)
        if negative:
            rate, text = -float(negative.group(1)), f"-{negative.group(1)}%"
    if rate is None:
        return None
    votes_match = _BOJ_VOTES.search(statement_body)
    against = _BOJ_AGAINST.search(statement_body)
    dissenters = (
        tuple(n.replace(_IIN, "").strip() for n in against.group(1).split(_TOUTEN) if n.strip())
        if against
        else ()
    )
    # The vote count beside the decision is authoritative; "unanimous" may
    # describe another item of the same statement.
    unanimous: bool | None = None
    if votes_match:
        unanimous = int(votes_match.group(2)) == 0
    elif _UNANIMOUS in statement_body:
        unanimous = True
    return Decision(
        text=text,
        low=rate,
        high=None,
        change_bp=round((rate - previous_rate) * 100) if previous_rate is not None else None,
        unanimous=unanimous,
        dissenters=dissenters,
        votes=f"{votes_match.group(1)}-{votes_match.group(2)}" if votes_match else None,
    )


def expected_on(institution: str, role: str, when: date) -> date | None:
    """The day a document of ``role`` is due for a meeting held on ``when``,
    or None when that meeting never produces one."""
    if institution == "fed":
        if role == "statement":
            return when
        if role == "minutes":
            return when + timedelta(days=21)
        if when.year >= 2019:
            return when if role == "press_conference" or when.month in (3, 6, 9, 12) else None
        # Press conferences and projections went together before 2019.
        months = {2011: (4, 6, 11), 2012: (1, 4, 6, 9, 12)}.get(when.year, (3, 6, 9, 12))
        return when if when.month in months else None
    if role in ("statement", "press_conference"):
        return when
    if role == "opinions":  # the Summary of Opinions began in 2016
        return when + timedelta(days=10) if when.year >= 2016 else None
    if role == "minutes":  # approved at the following meeting
        return when + timedelta(days=50)
    months = (1, 4, 7, 10) if when.year >= 2016 else (4, 10)
    return when if when.month in months else None


def part_status(
    institution: str, role: str, when: date, document: DocumentSummary | None, *, today: date
) -> str:
    if document is not None:
        return "present"
    due = expected_on(institution, role, when)
    if due is None:
        return "none"
    return "scheduled" if due >= today else "missing"  # released during the due day


def list_meetings(conn: Connection, *, limit: int = 40, today: date | None = None) -> list[Meeting]:
    today = today or date.today()
    titles = meeting_statement_titles()
    rows = conn.execute(
        """
        SELECT d.id, d.kind, d.external_id, d.published_at, d.title, d.meta, i.code AS institution,
               left(d.body, 6000) AS body_head
          FROM documents d JOIN institutions i ON i.id = d.institution_id
         WHERE i.code IN ('fed', 'boj')
           AND (
                (i.code = 'fed' AND (
                    d.kind IN ('statement', 'press_conference', 'minutes')
                 OR (d.kind = 'report' AND d.meta->>'subkind' = 'projections')))
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
        markers = titles.get(institution, ())
        if kind == "statement" and not any(m in row["title"] for m in markers):
            continue  # a statement that is not a policy decision
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
        elif meta.get("subkind") in ("outlook", "projections"):
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
    previous_rate: dict[str, float | None] = {}
    for meeting in reversed(selected):  # oldest first, so each change has its predecessor
        for attr in ("statement", "press_conference", "minutes", "opinions", "outlook"):
            doc = getattr(meeting, attr)
            if doc is not None and doc.id in full:
                setattr(meeting, attr, full[doc.id])
        meeting.parts = [
            Part(
                role,
                getattr(meeting, role),
                part_status(
                    meeting.institution, role, meeting.date, getattr(meeting, role), today=today
                ),
            )
            for role in ROLES[meeting.institution]
        ]
        meeting.documents = [p.document for p in meeting.parts if p.document is not None]
        body = bodies.get(meeting.statement.id) if meeting.statement else None
        if meeting.institution == "fed":
            meeting.decision = fed_decision(body)
        else:
            meeting.decision = boj_decision(body, previous_rate=previous_rate.get("boj"))
            if meeting.decision is not None:
                previous_rate["boj"] = meeting.decision.low
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
        speaker_ja=None,
        media_provider=None,
        media_id=None,
        duration_sec=None,
        timestamped=False,
    )
