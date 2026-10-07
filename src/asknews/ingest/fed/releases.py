"""FOMC statements, meeting minutes and Board speeches.

All three are ordinary article pages: the text sits in ``div#article`` with
the date in ``p.article__time`` and, for speeches, the speaker in
``p.speaker``. Statements and minutes are listed on the FOMC calendar pages;
speeches have one index page per year.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from asknews.db.connection import Connection, connect
from asknews.domain_config import SpeakerDirectory, speakers
from asknews.ingest.fed import BASE_URL, INSTITUTION
from asknews.ingest.fed.presconf import CALENDARS_URL, HISTORICAL_URL
from asknews.ingest.htmltext import content_window, drop_boilerplate, pack_paragraphs, paragraphs
from asknews.ingest.http import Fetcher, FetchError
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
KINDS = ("statement", "minutes", "speech")

_STATEMENT = re.compile(r"/newsevents/pressreleases/monetary(\d{8})a\.htm")
_MINUTES = re.compile(r"/monetarypolicy/fomcminutes(\d{8})\.htm")
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
    date: str  # YYYYMMDD


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


def list_statements_and_minutes(fetcher: Fetcher, *, since: int) -> list[Release]:
    pages = [fetcher.get(CALENDARS_URL).text]
    years = {
        int(d[:4]) for page in pages for d in _STATEMENT.findall(page) + _MINUTES.findall(page)
    }
    earliest = min(years, default=datetime.now(UTC).year)
    for year in range(since, earliest):
        try:
            pages.append(fetcher.get(HISTORICAL_URL.format(year=year)).text)
        except FetchError as exc:
            LOG.warning("no historical page for %s: %s", year, exc)
    releases: dict[tuple[str, str], Release] = {}
    for page in pages:
        for date in _STATEMENT.findall(page):
            path = f"/newsevents/pressreleases/monetary{date}a.htm"
            releases[("statement", date)] = Release("statement", BASE_URL + path, date, date)
        for date in _MINUTES.findall(page):
            path = f"/monetarypolicy/fomcminutes{date}.htm"
            releases[("minutes", date)] = Release("minutes", BASE_URL + path, date, date)
    return sorted(
        (r for r in releases.values() if int(r.date[:4]) >= since), key=lambda r: (r.date, r.kind)
    )


def list_speeches(fetcher: Fetcher, *, since: int) -> list[Release]:
    releases: dict[str, Release] = {}
    for year in range(since, datetime.now(UTC).year + 1):
        try:
            page = fetcher.get(SPEECHES_INDEX_URL.format(year=year)).text
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
            conn, institution=INSTITUTION, slug=spec.slug, name=spec.name, role=spec.role
        )
    return upsert_speaker(conn, institution=INSTITUTION, slug=url_surname, name=name, role=role)


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
    article = read_article(fetcher.get(release.url, refresh=refresh).text)
    if not article.paragraphs:
        raise ValueError(f"{release.url}: no article text found")
    title = _minutes_title(article) if release.kind == "minutes" else article.title
    published_at = datetime.strptime(release.date, "%Y%m%d").replace(tzinfo=UTC)
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
            kind=release.kind,
            lang="en",
            external_id=release.external_id,
            title=title or f"{release.kind} {release.date}",
            published_at=published_at,
            source_url=release.url,
            body="\n\n".join(article.paragraphs),
            meta={k: v for k, v in {"location": article.location}.items() if v},
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
        if kinds & {"statement", "minutes"}:
            releases += [
                r for r in list_statements_and_minutes(fetcher, since=args.since) if r.kind in kinds
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
        "fed-releases", help="FOMC statements, meeting minutes and Board speeches"
    )
    parser.add_argument("--kinds", nargs="+", choices=KINDS, default=list(KINDS))
    parser.add_argument("--since", type=int, default=2011, help="first year to include")
    parser.add_argument("--limit", type=int, default=0, help="only the N most recent")
    parser.add_argument("--refresh", action="store_true", help="ignore cached pages")
    parser.set_defaults(func=run)
