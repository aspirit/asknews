"""Titles of formulaic documents in the other language, from patterns.

The Bank of Japan and the Federal Reserve name their regular documents the
same way every time, and each has an official English rendering or an
established Japanese one. Those titles are written from patterns, without a
model, and stored as translations with the model name ``pattern``; the
summariser leaves such a title alone. Speeches and feed releases, whose
titles are free text, are left to the summariser.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime
from typing import Any

from asknews.db.connection import Connection, connect
from asknews.domain_config import SpeakerDirectory, speakers

PATTERN = "pattern"
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_C = chr
# Japanese words the patterns look for, written as code points to keep the
# source ASCII: year/month/day markers, punctuation, "Governor", "regular",
# "press conference", "Deputy Governor", "acting", "Board Member", "inaugural",
# "Monetary Policy Meeting", "minutes", "summary of opinions", "outlook",
# "the Bank's view", and the two statement titles.
_NEN, _GATSU, _NICHI = _C(0x5E74), _C(0x6708), _C(0x65E5)
_TOUTEN, _NAKAGURO = _C(0x3001), _C(0x30FB)
_OPEN, _CLOSE = _C(0xFF08), _C(0xFF09)
_SOUSAI = "".join(_C(c) for c in (0x7DCF, 0x88C1))
_TEIREI = "".join(_C(c) for c in (0x5B9A, 0x4F8B))
_KISHA_KAIKEN = "".join(_C(c) for c in (0x8A18, 0x8005, 0x4F1A, 0x898B))
_FUKU_SOUSAI = _C(0x526F) + _SOUSAI
_DAIRI = "".join(_C(c) for c in (0x4EE3, 0x7406))
_SHINGI_IIN = "".join(_C(c) for c in (0x5BE9, 0x8B70, 0x59D4, 0x54E1))
_SHUNIN = "".join(_C(c) for c in (0x5C31, 0x4EFB))
_MPM = "".join(_C(c) for c in (0x91D1, 0x878D, 0x653F, 0x7B56, 0x6C7A, 0x5B9A, 0x4F1A, 0x5408))
_GIJI_YOUSHI = "".join(_C(c) for c in (0x8B70, 0x4E8B, 0x8981, 0x65E8))
_OMONA_IKEN = "".join(
    _C(c) for c in (0x306B, 0x304A, 0x3051, 0x308B, 0x4E3B, 0x306A, 0x610F, 0x898B)
)
_TENBOU = "".join(
    _C(c) for c in (0x7D4C, 0x6E08, 0x30FB, 0x7269, 0x4FA1, 0x60C5, 0x52E2, 0x306E, 0x5C55, 0x671B)
)
_KIHONTEKI = "".join(_C(c) for c in (0x57FA, 0x672C, 0x7684, 0x898B, 0x89E3))
_TOUMEN = "".join(
    _C(c) for c in (0x5F53, 0x9762, 0x306E, 0x91D1, 0x878D, 0x653F, 0x7B56, 0x904B, 0x55B6)
)
_HENKOU = "".join(
    _C(c)
    for c in (
        0x91D1,
        0x878D,
        0x5E02,
        0x5834,
        0x8ABF,
        0x7BC0,
        0x65B9,
        0x91DD,
        0x306E,
        0x5909,
        0x66F4,
    )
)
_FOMC_KAIKEN = "FOMC" + _KISHA_KAIKEN
_FOMC_SEIMEI = "FOMC" + _C(0x58F0) + _C(0x660E)
_FOMC_YOUSHI = "FOMC" + _GIJI_YOUSHI
_KEIZAI_MITOSHI = "".join(_C(c) for c in (0x7D4C, 0x6E08, 0x898B, 0x901A, 0x3057))

_JA_FULL_DATE = re.compile(rf"(\d{{4}}){_NEN}(\d{{1,2}}){_GATSU}(\d{{1,2}}){_NICHI}")
_JA_MEETING_DAYS = re.compile(rf"(\d{{1,2}}){_GATSU}(\d{{1,2}})(?:{_TOUTEN}(\d{{1,2}}))?{_NICHI}")
_JA_YEAR_MONTH = re.compile(rf"(\d{{4}}){_NEN}(\d{{1,2}}){_GATSU}")
_MONTH_NAMES = "|".join(MONTHS)
_EN_DATE = re.compile(rf"({_MONTH_NAMES}) (\d{{1,2}}), (\d{{4}})")
_EN_DAYS = re.compile(rf"({_MONTH_NAMES}) (\d{{1,2}})(?:[{chr(0x2013)}-](\d{{1,2}}))?, (\d{{4}})")


def _en_date(year: int, month: int, day: int) -> str:
    return f"{MONTHS[month - 1]} {day}, {year}"


def _ja_date(year: int, month: int, day: int) -> str:
    return f"{year}{_NEN}{month}{_GATSU}{day}{_NICHI}"


def _person(directory: SpeakerDirectory, text: str) -> str | None:
    spec = directory.find("boj", text)
    return spec.name.split()[-1] if spec else None


def english_title(title: str, published: date, directory: SpeakerDirectory) -> str | None:
    """The English title of a formulaic Bank of Japan document, or None."""
    full = _JA_FULL_DATE.search(title)
    when = _en_date(*(int(g) for g in full.groups())) if full else None
    if _KISHA_KAIKEN in title and when:
        if _DAIRI in title:
            person = _person(directory, title.split(_OPEN, 1)[-1])
            who = f"Deputy Governor {person}" if person else "a Deputy Governor"
            return f"Press conference by {who}, for the Governor ({when})"
        if title.startswith(_SOUSAI):
            regular = _TEIREI in title
            return f"Governor's {'regular ' if regular else ''}press conference ({when})"
        person = _person(directory, title)
        if _FUKU_SOUSAI in title:
            role = "Deputy Governor"
        elif _SHINGI_IIN in title:
            role = "Board Member"
        else:
            return None
        inaugural = "Inaugural press" if _SHUNIN in title else "Press"
        return f"{inaugural} conference by {role} {person} ({when})" if person else None
    if title.startswith(_TOUMEN):
        return "Statement on Monetary Policy"
    if title.startswith(_HENKOU):
        return "Change in the Guideline for Money Market Operations"
    if title.startswith(_MPM + _GIJI_YOUSHI) or title.startswith(_MPM + _OMONA_IKEN):
        days = _JA_MEETING_DAYS.search(title)
        label = (
            "Minutes of the Monetary Policy Meeting"
            if _GIJI_YOUSHI in title
            else "Summary of Opinions at the Monetary Policy Meeting"
        )
        if not days:
            return label
        month, first, last = int(days.group(1)), int(days.group(2)), days.group(3)
        year = published.year - 1 if month > published.month else published.year
        span = f"{MONTHS[month - 1]} {first}" + (f"{chr(0x2013)}{last}" if last else "")
        return f"{label} ({span}, {year})"
    if title.startswith(_TENBOU):
        ym = _JA_YEAR_MONTH.search(title)
        view = ": The Bank's View" if _KIHONTEKI in title else ""
        if ym:
            month_name = MONTHS[int(ym.group(2)) - 1]
            return f"Outlook for Economic Activity and Prices ({month_name} {ym.group(1)}){view}"
        return f"Outlook for Economic Activity and Prices{view}"
    return None


def japanese_title(title: str, published: date, kind: str) -> str | None:
    """The Japanese title of a formulaic Federal Reserve document, or None."""
    when = _ja_date(published.year, published.month, published.day)
    if kind == "press_conference" and ("Press Conference" in title or "press conference" in title):
        found = _EN_DATE.search(title)
        if found:
            when = _ja_date(
                int(found.group(3)), MONTHS.index(found.group(1)) + 1, int(found.group(2))
            )
        return f"{_FOMC_KAIKEN}{_OPEN}{when}{_CLOSE}"
    if kind == "statement" and "FOMC statement" in title:
        return f"{_FOMC_SEIMEI}{_OPEN}{when}{_CLOSE}"
    if kind == "minutes" and title.startswith("Minutes of the Federal Open Market Committee"):
        days = _EN_DAYS.search(title)
        if days:
            month = MONTHS.index(days.group(1)) + 1
            span = days.group(2) + (f"{chr(0x2013)}{days.group(3)}" if days.group(3) else "")
            return (
                f"{_FOMC_YOUSHI}{_OPEN}{days.group(4)}{_NEN}{month}{_GATSU}{span}{_NICHI}{_CLOSE}"
            )
        return _FOMC_YOUSHI
    if kind == "report" and title.startswith("Summary of Economic Projections"):
        found = _EN_DATE.search(title)
        if found:
            when = _ja_date(
                int(found.group(3)), MONTHS.index(found.group(1)) + 1, int(found.group(2))
            )
        return f"{_KEIZAI_MITOSHI}{_OPEN}SEP{_NAKAGURO}{when}{_CLOSE}"
    return None


def write_pattern_titles(conn: Connection, directory: SpeakerDirectory) -> int:
    """Store a pattern title for every core document that has none of this
    kind yet. Returns the number written."""
    rows = conn.execute(
        """
        SELECT d.id, d.title, d.lang, d.kind, d.published_at, i.code AS institution
          FROM documents d JOIN institutions i ON i.id = d.institution_id
         WHERE i.code IN ('boj', 'fed')
           AND d.kind IN ('press_conference', 'statement', 'minutes', 'report')
           AND NOT EXISTS (
                SELECT 1 FROM document_translations t
                 WHERE t.document_id = d.id AND t.model = %s)
        """,
        (PATTERN,),
    ).fetchall()
    done = 0
    for row in rows:
        published: datetime = row["published_at"]
        if row["institution"] == "boj":
            other, rendered = "en", english_title(row["title"], published.date(), directory)
        else:
            other, rendered = "ja", japanese_title(row["title"], published.date(), row["kind"])
        if rendered is None or other == row["lang"]:
            continue
        conn.execute(
            """
            INSERT INTO document_translations (document_id, lang, title, summary, model)
            VALUES (%s, %s, %s, NULL, %s)
            ON CONFLICT (document_id, lang) DO UPDATE
               SET title = EXCLUDED.title, model = EXCLUDED.model, generated_at = now()
            """,
            (row["id"], other, rendered, PATTERN),
        )
        done += 1
    return done


def run(_args: argparse.Namespace) -> int:
    with connect(autocommit=True) as conn:
        done = write_pattern_titles(conn, speakers())
    sys.stdout.write(f"{done} titles written from patterns\n")
    return 0


def add_cli(subparsers: Any) -> None:
    parser = subparsers.add_parser("titles", help="title translations of formulaic documents")
    parser.set_defaults(func=run)
