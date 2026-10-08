"""Documents from RSS and Atom feeds of publishers outside the core corpus.

Feeds are rolling windows, so this job is incremental by nature: it stores
whatever the feed currently lists (optionally only the last N days). What a
source contributes beyond title and link is decided in ``domain/feeds.yaml``,
because publishers differ in what their terms allow.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import logging
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import yaml

from asknews.db.connection import Connection, connect
from asknews.ingest.boj import normalise as normalise_ja
from asknews.ingest.boj import pack_ja
from asknews.ingest.boj import pdf_paragraphs as boj_pdf_paragraphs
from asknews.ingest.htmltext import content_window, drop_boilerplate, pack_paragraphs, paragraphs
from asknews.ingest.http import Fetched, Fetcher, FetchError
from asknews.ingest.pdf import pdf_text
from asknews.ingest.store import Document, Segment, replace_segments, upsert_document
from asknews.settings import get_settings

LOG = logging.getLogger(__name__)

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "rss1": "http://purl.org/rss/1.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}
_TAGS = re.compile(r"<[^>]+>")


@dataclass(frozen=True, slots=True)
class Source:
    code: str
    institution: str
    url: str
    lang: str
    kind: str
    body: str  # page | description | none
    main: tuple[str, str] | None = None
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    drop: tuple[str, ...] = ()
    title_strip: tuple[str, ...] = ()  # regular expressions removed from titles
    # Where the date an item is about is named, when that is not the posting
    # date: "title" (a conference summary titled with its day) or
    # "description" (a speech library naming the day it was given). None
    # means the item is about the day it was posted.
    event: str | None = None

    def accepts(self, link: str) -> bool:
        if self.include and not any(re.search(p, link) for p in self.include):
            return False
        return not any(re.search(p, link) for p in self.exclude)


@dataclass(frozen=True, slots=True)
class Entry:
    title: str
    link: str
    published: datetime | None
    description: str = ""
    creator: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Outcome:
    source: str
    document_id: int
    segments: int
    changed: bool


_MONTHS = (
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
_MONTH_NAMES = "|".join(_MONTHS)
_EN_DAY_FIRST = re.compile(rf"\b(\d{{1,2}}) ({_MONTH_NAMES}) (\d{{4}})\b")
_EN_MONTH_FIRST = re.compile(rf"\b({_MONTH_NAMES}) (\d{{1,2}}), (\d{{4}})\b")
_NEN, _GATSU, _NICHI = chr(0x5E74), chr(0x6708), chr(0x65E5)
_REIWA = chr(0x4EE4) + chr(0x548C)  # the current Japanese era, from 2019
_JA_YMD = re.compile(rf"(\d{{4}})\s*{_NEN}\s*(\d{{1,2}})\s*{_GATSU}\s*(\d{{1,2}})\s*{_NICHI}")
_JA_REIWA = re.compile(
    rf"{_REIWA}\s*(\d{{1,2}})\s*{_NEN}\s*(\d{{1,2}})\s*{_GATSU}\s*(\d{{1,2}})\s*{_NICHI}"
)
_EVENT_LOOKBACK = timedelta(days=120)


def _dates_in(text: str) -> list[date]:
    found: list[tuple[int, date]] = []
    for pattern in (_JA_REIWA, _JA_YMD, _EN_DAY_FIRST, _EN_MONTH_FIRST):
        for match in pattern.finditer(text):
            try:
                if pattern is _JA_REIWA:
                    when = date(
                        2018 + int(match.group(1)), int(match.group(2)), int(match.group(3))
                    )
                elif pattern is _JA_YMD:
                    when = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
                elif pattern is _EN_DAY_FIRST:
                    when = date(
                        int(match.group(3)), _MONTHS.index(match.group(2)) + 1, int(match.group(1))
                    )
                else:
                    when = date(
                        int(match.group(3)), _MONTHS.index(match.group(1)) + 1, int(match.group(2))
                    )
            except ValueError:
                continue
            found.append((match.start(), when))
    return [when for _, when in sorted(found)]


def event_date(text: str, *, posted: date) -> date | None:
    """The date an item is about, when the given text names one: the first
    date within the previous four months. Called only on the field a source
    designates, since a body may mention any number of earlier dates."""
    for when in _dates_in(text):
        if posted - _EVENT_LOOKBACK <= when <= posted:
            return when
    return None


def load_sources(path: Path) -> list[Source]:
    raw = yaml.safe_load(path.read_text("utf-8")) or {}
    sources: list[Source] = []
    for item in raw.get("sources") or []:
        main = item.get("main")
        sources.append(
            Source(
                code=str(item["code"]),
                institution=str(item["institution"]),
                url=str(item["url"]),
                lang=str(item["lang"]),
                kind=str(item.get("kind", "release")),
                body=str(item.get("body", "description")),
                main=(str(main[0]), str(main[1])) if main else None,
                include=tuple(str(p) for p in item.get("include") or ()),
                exclude=tuple(str(p) for p in item.get("exclude") or ()),
                drop=tuple(str(p) for p in item.get("drop") or ()),
                title_strip=tuple(str(p) for p in item.get("title_strip") or ()),
                event=_event_field(item.get("event")),
            )
        )
    return sources


def _event_field(value: object) -> str | None:
    if value is None:
        return None
    if value not in ("title", "description"):
        raise ValueError(f"event must be 'title' or 'description', not {value!r}")
    return str(value)


def _text(element: ET.Element | None) -> str:
    """Element text with markup (CDATA often carries HTML) and entities removed."""
    if element is None:
        return ""
    raw = html.unescape(_TAGS.sub(" ", "".join(element.itertext())))
    return re.sub(r"\s+", " ", raw).strip()


def parse_date(value: str) -> datetime | None:
    """RFC 822 (RSS 2.0) or ISO 8601 (Atom, Dublin Core), always timezone-aware."""
    value = value.strip()
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _rss_entry(item: ET.Element) -> Entry:
    return Entry(
        title=_text(item.find("title")),
        link=_text(item.find("link")) or _text(item.find("guid")),
        published=parse_date(_text(item.find("pubDate")) or _text(item.find("dc:date", _NS))),
        description=_text(item.find("description")),
        creator=_text(item.find("dc:creator", _NS)) or None,
    )


def _rdf_entry(item: ET.Element) -> Entry:
    return Entry(
        title=_text(item.find("rss1:title", _NS)),
        link=_text(item.find("rss1:link", _NS)),
        published=parse_date(_text(item.find("dc:date", _NS))),
        description=_text(item.find("rss1:description", _NS)),
        creator=_text(item.find("dc:creator", _NS)) or None,
    )


def _atom_entry(item: ET.Element) -> Entry:
    link = item.find("atom:link[@rel='alternate']", _NS)
    if link is None:
        link = item.find("atom:link", _NS)
    youtube = "{http://www.youtube.com/xml/schemas/2015}"
    return Entry(
        title=_text(item.find("atom:title", _NS)),
        link=(link.get("href") if link is not None else "") or "",
        published=parse_date(
            _text(item.find("atom:published", _NS)) or _text(item.find("atom:updated", _NS))
        ),
        description=_text(item.find("atom:summary", _NS)),
        creator=_text(item.find("atom:author/atom:name", _NS)) or None,
        extra={
            child.tag[len(youtube) :]: (child.text or "")
            for child in item
            if child.tag.startswith(youtube)
        },
    )


def parse_feed(xml: str) -> list[Entry]:
    """Entries of an RSS 2.0, RSS 1.0 (RDF) or Atom document."""
    root = ET.fromstring(xml.lstrip(chr(0xFEFF)).encode("utf-8"))
    if root.tag == "rss":
        entries = [_rss_entry(item) for item in root.iter("item")]
    elif root.tag.endswith("RDF"):
        entries = [_rdf_entry(item) for item in root.findall("rss1:item", _NS)]
    elif root.tag == f"{{{_NS['atom']}}}feed":
        entries = [_atom_entry(item) for item in root.findall("atom:entry", _NS)]
    else:
        entries = []
    return [e for e in entries if e.link]


def clean_title(title: str, source: Source) -> str:
    """A feed title without the source's category prefixes and stock phrases."""
    for pattern in source.title_strip:
        title = re.sub(pattern, "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return normalise_ja(title) if source.lang == "ja" else title


def external_id(link: str) -> str:
    return hashlib.sha1(link.encode("utf-8")).hexdigest()[:16]


def _page_paragraphs(markup: str, source: Source) -> list[str]:
    window = (
        content_window(markup, start=[source.main[0]], end=[source.main[1]])
        if source.main
        else markup
    )
    items = drop_boilerplate(paragraphs(window), drop=source.drop)
    if source.lang == "ja":
        items = [normalise_ja(p) for p in items]
    return items


def _is_pdf(fetched: Fetched) -> bool:
    """By the declared type or the file's own signature: a link's extension
    and a server's Content-Type are both unreliable."""
    declared = (fetched.content_type or "").split(";")[0].strip().lower()
    return declared == "application/pdf" or fetched.content[:5] == b"%PDF-"


def _pdf_paragraphs(text: str, lang: str) -> list[str]:
    """Paragraphs from a PDF's text; empty when it had no readable text."""
    if not text:
        return []
    if lang == "ja":
        return boj_pdf_paragraphs(text)
    blocks = re.split(r"\n\s*\n", text)
    return [re.sub(r"\s*\n\s*", " ", b).strip() for b in blocks if b.strip()]


def body_paragraphs(
    fetcher: Fetcher, source: Source, entry: Entry, *, refresh: bool
) -> tuple[list[str], str]:
    """The paragraphs stored for an entry and where they came from:
    ``page`` (the linked page's text), ``description`` (the feed's own
    blurb) or ``title_only`` (nothing, by the publisher's terms)."""
    if source.body == "none":
        return [], "title_only"
    if source.body == "page":
        try:
            fetched = fetcher.get(entry.link, refresh=refresh)
        except FetchError as exc:
            LOG.warning("%s: page unavailable, using description: %s", entry.link, exc)
        else:
            if _is_pdf(fetched):
                items = _pdf_paragraphs(pdf_text(fetched.content), source.lang)
            else:
                items = _page_paragraphs(fetched.text, source)
            if sum(len(p) for p in items) >= 200:
                return items, "page"
    description = _TAGS.sub(" ", entry.description)
    description = re.sub(r"\s+", " ", description).strip()
    return ([description] if description else []), "description"


def ingest_entry(
    conn: Connection, fetcher: Fetcher, source: Source, entry: Entry, *, refresh: bool = False
) -> Outcome:
    items, origin = body_paragraphs(fetcher, source, entry, refresh=refresh)
    body = "\n\n".join(items) if items else None
    meta: dict[str, Any] = {"feed": source.code}
    if entry.creator:
        meta["creator"] = entry.creator
    if origin != "page":
        meta["text"] = origin
    # The feed's own date is the publication date. A date the item is about
    # (the day of a conference or a speech) is kept beside it, never in its place.
    published_at = entry.published or datetime.now(UTC)
    if entry.published is None:
        meta["undated"] = True
    if source.event:
        named = entry.title if source.event == "title" else entry.description
        when = event_date(named, posted=published_at.date())
        if when is not None and when != published_at.date():
            meta["event_date"] = when.isoformat()
    document_id = upsert_document(
        conn,
        Document(
            institution=source.institution,
            kind=source.kind,
            lang=source.lang,
            external_id=external_id(entry.link),
            title=clean_title(entry.title, source) or entry.link,
            published_at=published_at,
            source_url=entry.link,
            body=body,
            meta=meta,
        ),
    )
    chunks = pack_ja(items) if source.lang == "ja" else pack_paragraphs(items)
    segments = [Segment(i, text) for i, text in enumerate(chunks)]
    changed = replace_segments(conn, document_id, segments)
    return Outcome(source.code, document_id, len(segments), changed)


def recent(entries: Iterable[Entry], *, days: int) -> list[Entry]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    return [e for e in entries if e.published is None or e.published >= cutoff]


def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    sources = load_sources(settings.domain_dir / "feeds.yaml")
    if args.sources:
        sources = [s for s in sources if s.code in set(args.sources)]
    failures = 0
    with Fetcher(settings.data_dir / "raw") as fetcher, connect(autocommit=True) as conn:
        for source in sources:
            try:
                entries = parse_feed(fetcher.get(source.url, refresh=True).text)
            except (FetchError, ET.ParseError) as exc:
                failures += 1
                LOG.warning("%s: feed unavailable: %s", source.code, exc)
                continue
            selected = [e for e in recent(entries, days=args.days) if source.accepts(e.link)]
            stored = updated = 0
            for entry in selected:
                try:
                    outcome = ingest_entry(conn, fetcher, source, entry, refresh=args.refresh)
                except FetchError as exc:
                    LOG.warning("%s: %s", entry.link, exc)
                    continue
                stored += 1
                updated += int(outcome.changed)
            sys.stdout.write(
                f"{source.code:14s}  {len(entries):3d} in feed  {len(selected):3d} selected  "
                f"{stored:3d} stored  {updated:3d} updated\n"
            )
    return 1 if failures else 0


def add_cli(subparsers: Any) -> None:
    parser = subparsers.add_parser("feeds", help="RSS/Atom feeds of other publishers")
    parser.add_argument("--sources", nargs="*", help="feed codes from domain/feeds.yaml")
    parser.add_argument("--days", type=int, default=10, help="only entries from the last N days")
    parser.add_argument("--refresh", action="store_true", help="ignore cached pages")
    parser.set_defaults(func=run)
