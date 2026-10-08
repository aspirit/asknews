"""FOMC statements, meeting minutes, projection materials and Board speeches.

Statements, minutes and speeches are ordinary article pages: the text sits
in ``div#article`` with the date in ``p.article__time`` and, for speeches,
the speaker in ``p.speaker``. The projection materials page is a set of
tables, of which Table 1 (the medians) is kept as text. Statements, minutes
and projections are listed on the FOMC calendar pages, which also give the
release date of each set of minutes; speeches have one index page per year.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from asknews.db.connection import Connection, connect
from asknews.domain_config import SpeakerDirectory, speakers
from asknews.ingest.fed import BASE_URL, INSTITUTION
from asknews.ingest.fed.presconf import CALENDARS_URL, HISTORICAL_URL
from asknews.ingest.htmltext import (
    content_window,
    drop_boilerplate,
    pack_paragraphs,
    paragraphs,
    table_rows,
)
from asknews.ingest.http import INDEX_MAX_AGE, Fetcher, FetchError
from asknews.ingest.store import (
    Document,
    Segment,
    replace_segments,
    upsert_document,
    upsert_speaker,
)
from asknews.settings import get_settings

LOG = logging.getLogger(__name__)

SPEECHES_INDEX_URL = BASE_URL + "/newsevents/speech/{year}-speeches.htm"
KINDS = ("statement", "minutes", "projections", "speech")
# Board publications go out in the Washington afternoon; the hour keeps the
# calendar date right in every time zone the interface shows.
RELEASE_HOUR_UTC = 18

_STATEMENT = re.compile(r"/newsevents/pressreleases/monetary(\d{8})a\.htm")
_MINUTES = re.compile(r"/monetarypolicy/fomcminutes(\d{8})\.htm")
_RELEASED = re.compile(
    r"fomcminutes(\d{8})\.htm[^(]{0,160}\(Released ([A-Z][a-z]+ \d{1,2}, \d{4})\)", re.S
)
# One set of projection materials is linked under a misspelt name ("projtable"),
# so the link is kept as found rather than rebuilt from the date.
_PROJECTIONS = re.compile(r"(/monetarypolicy/fomcprojtable?(\d{8})\.htm)")
_LAST_UPDATE = re.compile(r"Last Update:\s*([A-Z][a-z]+ \d{1,2}, \d{4})")
_YEAR = re.compile(r"\d{4}")
_SPEECH = re.compile(r"/newsevents/speech/([a-z]+)(\d{8})([a-z])\.htm")
_TITLE = re.compile(r"<h3[^>]*class=['\"]title['\"][^>]*>(.*?)</h3>", re.S)
_ANY_H3 = re.compile(r"<h3[^>]*>(.*?)</h3>", re.S)
_SPEAKER = re.compile(r"<p[^>]*class=['\"]speaker['\"][^>]*>(.*?)</p>", re.S)
_LOCATION = re.compile(r"<p[^>]*class=['\"]location['\"][^>]*>(.*?)</p>", re.S)
_TAGS = re.compile(r"<[^>]+>")
_DATE_TEXT = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|November|December)"
    r" \d{1,2}, \d{4}$"
)
_ROLE = re.compile(
    r"^(Chair(?:man|woman)?|Vice Chair(?:man|woman)?(?: for Supervision)?|Governor)\s+(.+)$"
)

_BOILERPLATE = (
    "Share",
    "For release at",
    "For immediate release",
    "For media inquiries",
    "Implementation Note issued",
    "Accessible Version",
    "View speech charts",
    "Watch Live",
    "Last Update",
    "Back to Top",
)


@dataclass(frozen=True, slots=True)
class Release:
    kind: str
    url: str
    external_id: str
    date: str  # YYYYMMDD, the meeting or speech date
    released: str | None = None  # YYYYMMDD when the calendar gives a later release date
    unscheduled: bool = False  # the calendar marks the meeting "(unscheduled)"


@dataclass(frozen=True, slots=True)
class Article:
    title: str
    date: str | None
    speaker: str | None
    location: str | None
    paragraphs: list[str]


@dataclass(frozen=True, slots=True)
class Outcome:
    release: Release
    document_id: int
    segments: int
    changed: bool


def _strip(markup: str) -> str:
    return re.sub(r"\s+", " ", _TAGS.sub("", markup)).strip()


def _unscheduled(page: str, position: int) -> bool:
    """Whether the meeting block a statement link sits in is marked unscheduled.

    Historical pages head each meeting with an ``h5``; the current calendar
    uses a ``fomc-meeting`` block. The heading text carries "(unscheduled)".
    """
    starts = ("<h5", "<h4", "panel panel-default", "fomc-meeting")
    start = max(page.rfind(marker, 0, position) for marker in starts)
    return "unscheduled" in page[max(start, 0) : position].lower()


def _ymd(text: str) -> str:
    return datetime.strptime(text, "%B %d, %Y").strftime("%Y%m%d")


def list_calendar_releases(fetcher: Fetcher, *, since: int) -> list[Release]:
    """Statements, minutes and projection materials from the FOMC calendar pages."""
    pages = [fetcher.get(CALENDARS_URL, max_age=INDEX_MAX_AGE).text]
    years = {
        int(d[:4]) for page in pages for d in _STATEMENT.findall(page) + _MINUTES.findall(page)
    }
    earliest = min(years, default=datetime.now(UTC).year)
    for year in range(since, earliest):
        try:
            pages.append(fetcher.get(HISTORICAL_URL.format(year=year), max_age=INDEX_MAX_AGE).text)
        except FetchError as exc:
            LOG.warning("no historical page for %s: %s", year, exc)
    releases: dict[tuple[str, str], Release] = {}
    for page in pages:
        released = {date: _ymd(text) for date, text in _RELEASED.findall(page)}
        for match in _STATEMENT.finditer(page):
            date = match.group(1)
            path = f"/newsevents/pressreleases/monetary{date}a.htm"
            releases[("statement", date)] = Release(
                "statement",
                BASE_URL + path,
                date,
                date,
                unscheduled=_unscheduled(page, match.start()),
            )
        for date in _MINUTES.findall(page):
            path = f"/monetarypolicy/fomcminutes{date}.htm"
            releases[("minutes", date)] = Release(
                "minutes", BASE_URL + path, date, date, released=released.get(date)
            )
        for path, date in _PROJECTIONS.findall(page):
            releases[("projections", date)] = Release("projections", BASE_URL + path, date, date)
    return sorted(
        (r for r in releases.values() if int(r.date[:4]) >= since), key=lambda r: (r.date, r.kind)
    )


def list_speeches(fetcher: Fetcher, *, since: int) -> list[Release]:
    releases: dict[str, Release] = {}
    for year in range(since, datetime.now(UTC).year + 1):
        try:
            page = fetcher.get(SPEECHES_INDEX_URL.format(year=year), max_age=INDEX_MAX_AGE).text
        except FetchError as exc:
            LOG.warning("no speeches index for %s: %s", year, exc)
            continue
        for surname, date, suffix in _SPEECH.findall(page):
            stem = f"{surname}{date}{suffix}"
            path = f"/newsevents/speech/{stem}.htm"
            releases[stem] = Release("speech", BASE_URL + path, stem, date)
    return sorted(releases.values(), key=lambda r: (r.date, r.external_id))


def read_article(markup: str) -> Article:
    window = content_window(
        markup,
        start=['id="article"', "id='article'"],
        end=['id="lastUpdate"', "id='lastUpdate'", "panel-attachments"],
    )
    title_match = _TITLE.search(window) or _ANY_H3.search(window)
    title = _strip(title_match.group(1)) if title_match else ""
    speaker_match = _SPEAKER.search(window)
    location_match = _LOCATION.search(window)
    speaker = _strip(speaker_match.group(1)) if speaker_match else None
    location = _strip(location_match.group(1)) if location_match else None
    items = drop_boilerplate(paragraphs(window), drop=_BOILERPLATE)
    date = next((p for p in items if _DATE_TEXT.match(p)), None)
    header = {p for p in (title, date, speaker, location) if p}
    body = [p for p in items if p not in header]
    return Article(title, date, speaker, location, body)


def projection_lines(rows: Sequence[Sequence[str]]) -> list[str]:
    """Table 1 as one line per variable with its median projections.

    The header row of years is shared by the median, central tendency and
    range groups; only the medians are kept. Comparison rows from the
    previous round ("June projection") are left out.
    """
    years_at = next((i for i, row in enumerate(rows) if row and _YEAR.fullmatch(row[0])), None)
    if years_at is None:
        return []
    header = rows[years_at]
    width = len(header) // 3 or len(header)
    years = header[:width]
    lines: list[str] = []
    for row in rows[years_at + 1 :]:
        if len(row) < 1 + width or row[0].lower().endswith("projection"):
            continue
        values = [(y, v) for y, v in zip(years, row[1 : 1 + width], strict=True) if v]
        if values:
            lines.append(f"{row[0]}: " + "; ".join(f"{y} {v}" for y, v in values))
    return lines


def read_projections(markup: str) -> Article:
    """The projection materials page: its introduction and Table 1 as text."""
    window = content_window(
        markup, start=['id="content"'], end=['id="lastUpdate"', "id='lastUpdate'", "<footer"]
    )
    table_at = window.find("<table")
    intro_markup = window[:table_at] if table_at > 0 else window
    intro = [p for p in paragraphs(intro_markup) if len(p) > 60 and "materials" not in p]
    rows = table_rows(window[table_at:]) if table_at > 0 else []
    return Article(
        "Summary of Economic Projections", None, None, None, [*intro, *projection_lines(rows)]
    )


def split_speaker(text: str) -> tuple[str | None, str]:
    """``Governor Stephen I. Miran`` -> ``("Governor", "Stephen I. Miran")``."""
    match = _ROLE.match(text.strip())
    return (match.group(1), match.group(2).strip()) if match else (None, text.strip())


def _speaker_id(
    conn: Connection, directory: SpeakerDirectory, article: Article, url_surname: str
) -> int | None:
    if not article.speaker:
        return None
    role, name = split_speaker(article.speaker)
    spec = directory.find(INSTITUTION, name)
    if spec is not None:
        return upsert_speaker(
            conn,
            institution=INSTITUTION,
            slug=spec.slug,
            name=spec.name,
            role=spec.role,
            name_ja=spec.name_ja,
        )
    return upsert_speaker(conn, institution=INSTITUTION, slug=url_surname, name=name, role=role)


def release_date(release: Release, markup: str) -> datetime:
    """When the item was published.

    Minutes appear three weeks after the meeting: the calendar's release
    date is used, else the page's "Last Update" line. Everything else is
    dated by the meeting or speech day.
    """
    ymd = release.released
    if ymd is None and release.kind == "minutes":
        found = _LAST_UPDATE.search(markup)
        ymd = _ymd(found.group(1)) if found else None
    return datetime.strptime(ymd or release.date, "%Y%m%d").replace(
        hour=RELEASE_HOUR_UTC, tzinfo=UTC
    )


def _minutes_title(article: Article) -> str:
    # The h3 is generic; the meeting dates sit in the first paragraph's bold lead.
    if article.paragraphs:
        lead = article.paragraphs[0].split(" A joint meeting", 1)[0].split(" A meeting", 1)[0]
        if len(lead) < 60:
            return f"{article.title}, {lead.strip()}"
    return article.title


def ingest_release(
    conn: Connection,
    fetcher: Fetcher,
    release: Release,
    *,
    directory: SpeakerDirectory,
    refresh: bool = False,
) -> Outcome:
    markup = fetcher.get(release.url, refresh=refresh).text
    article = read_projections(markup) if release.kind == "projections" else read_article(markup)
    if not article.paragraphs:
        raise ValueError(f"{release.url}: no article text found")
    kind = "report" if release.kind == "projections" else release.kind
    meta: dict[str, Any] = {}
    if release.kind == "minutes":
        title = _minutes_title(article)
    elif release.kind == "projections":
        when = datetime.strptime(release.date, "%Y%m%d")
        title = f"{article.title}, {when:%B} {when.day}, {when.year}"
        meta["subkind"] = "projections"
    else:
        title = article.title
    if article.location:
        meta["location"] = article.location
    if release.unscheduled:
        meta["unscheduled"] = True
    published_at = release_date(release, markup)
    url_surname = _SPEECH.search(release.url)
    speaker_id = (
        _speaker_id(conn, directory, article, url_surname.group(1))
        if release.kind == "speech" and url_surname
        else None
    )
    document_id = upsert_document(
        conn,
        Document(
            institution=INSTITUTION,
            kind=kind,
            lang="en",
            external_id=release.external_id,
            title=title or f"{release.kind} {release.date}",
            published_at=published_at,
            source_url=release.url,
            body="\n\n".join(article.paragraphs),
            meta=meta,
            speaker_id=speaker_id,
        ),
    )
    segments = [Segment(i, text) for i, text in enumerate(pack_paragraphs(article.paragraphs))]
    changed = replace_segments(conn, document_id, segments)
    return Outcome(release, document_id, len(segments), changed)


def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    directory = speakers()
    kinds = set(args.kinds)
    with Fetcher(settings.data_dir / "raw") as fetcher, connect(autocommit=True) as conn:
        releases: list[Release] = []
        if kinds & {"statement", "minutes", "projections"}:
            releases += [
                r for r in list_calendar_releases(fetcher, since=args.since) if r.kind in kinds
            ]
        if "speech" in kinds:
            releases += list_speeches(fetcher, since=args.since)
        releases.sort(key=lambda r: r.date)
        if args.limit:
            releases = releases[-args.limit :]
        failures = 0
        for release in releases:
            try:
                outcome = ingest_release(
                    conn, fetcher, release, directory=directory, refresh=args.refresh
                )
            except (FetchError, ValueError) as exc:
                failures += 1
                LOG.warning("%s skipped: %s", release.external_id, exc)
                continue
            state = "updated" if outcome.changed else "unchanged"
            sys.stdout.write(
                f"{release.date}  {release.kind:9s}  doc {outcome.document_id}  "
                f"{outcome.segments:3d} segments  {state}\n"
            )
    return 1 if failures else 0


def add_cli(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "fed-releases", help="FOMC statements, minutes, projection materials and Board speeches"
    )
    parser.add_argument("--kinds", nargs="+", choices=KINDS, default=list(KINDS))
    parser.add_argument("--since", type=int, default=2011, help="first year to include")
    parser.add_argument("--limit", type=int, default=0, help="only the N most recent")
    parser.add_argument("--refresh", action="store_true", help="ignore cached pages")
    parser.set_defaults(func=run)
