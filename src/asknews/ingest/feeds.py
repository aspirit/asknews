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
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import yaml

from asknews.db.connection import Connection, connect
from asknews.ingest.boj import normalise as normalise_ja
from asknews.ingest.boj import pack_ja
from asknews.ingest.htmltext import content_window, drop_boilerplate, pack_paragraphs, paragraphs
from asknews.ingest.http import Fetcher, FetchError
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
            )
        )
    return sources


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


def body_paragraphs(fetcher: Fetcher, source: Source, entry: Entry, *, refresh: bool) -> list[str]:
    if source.body == "none":
        return []
    if source.body == "page":
        try:
            page = fetcher.get(entry.link, refresh=refresh).text
        except FetchError as exc:
            LOG.warning("%s: page unavailable, using description: %s", entry.link, exc)
        else:
            items = _page_paragraphs(page, source)
            if sum(len(p) for p in items) >= 200:
                return items
    description = _TAGS.sub(" ", entry.description)
    description = re.sub(r"\s+", " ", description).strip()
    return [description] if description else []


def ingest_entry(
    conn: Connection, fetcher: Fetcher, source: Source, entry: Entry, *, refresh: bool = False
) -> Outcome:
    items = body_paragraphs(fetcher, source, entry, refresh=refresh)
    body = "\n\n".join(items) if items else None
    meta: dict[str, Any] = {"feed": source.code}
    if entry.creator:
        meta["creator"] = entry.creator
    if source.body == "none":
        meta["text"] = "title_only"
    document_id = upsert_document(
        conn,
        Document(
            institution=source.institution,
            kind=source.kind,
            lang=source.lang,
            external_id=external_id(entry.link),
            title=clean_title(entry.title, source) or entry.link,
            published_at=entry.published or datetime.now(UTC),
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
