"""Bank of Japan statements, minutes, outlook reports and speeches.

Everything is reached from a yearly index page:

* statements and operational notices from the monetary policy releases
  index: PDFs up to 2017, pages since 2018;
* minutes and the Summary of Opinions from their yearly indexes, whose rows
  give the publication date and the meeting dates; minutes are pages since
  2018, the Summary of Opinions stays a PDF;
* the Outlook Report's basic view from the outlook index (PDF);
* speeches, whose index rows name the speaker and whose pages link the PDF.
"""

from __future__ import annotations

import argparse
import html
import logging
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from asknews.db.connection import Connection, connect
from asknews.domain_config import SpeakerDirectory, speakers
from asknews.ingest.boj import (
    BASE_URL,
    INSTITUTION,
    clean_title,
    normalise,
    pack_ja,
    parse_ja_date,
    pdf_paragraphs,
)
from asknews.ingest.htmltext import content_window, paragraphs
from asknews.ingest.http import Fetcher, FetchError
from asknews.ingest.pdf import pdf_text
from asknews.ingest.store import (
    Document,
    Segment,
    replace_segments,
    upsert_document,
    upsert_speaker,
)
from asknews.settings import get_settings

LOG = logging.getLogger(__name__)

RELEASES_INDEX_URL = BASE_URL + "/mopo/mpmdeci/mpr_{year}/index.htm"
MINUTES_INDEX_URL = BASE_URL + "/mopo/mpmsche_minu/minu_{year}/index.htm"
OPINIONS_INDEX_URL = BASE_URL + "/mopo/mpmsche_minu/opinion_{year}/index.htm"
OUTLOOK_INDEX_URL = BASE_URL + "/mopo/outlook/index.htm"
SPEECHES_INDEX_URL = BASE_URL + "/about/press/koen_{year}/index.htm"
FIRST_YEAR = 2011
JST = timezone(timedelta(hours=9))
KINDS = ("statement", "release", "minutes", "opinions", "outlook", "speech")

_TAGS = re.compile(r"<[^>]+>")
_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL_LINK = re.compile(r"<td[^>]*>\s*<a href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.S)
_DATED_ROW = re.compile(r"<td>([^<]*)</td>\s*<td><a href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.S)
_STATEMENT = re.compile(r"/mopo/mpmdeci/mpr_\d{4}/(k\d{6}a)\.pdf$")
_STATEMENT_PAGE = re.compile(r"/mopo/mpmdeci/state_\d{4}/(k\d{6}a)\.htm$")
_RELEASE = re.compile(r"/mopo/mpmdeci/mpr_\d{4}/((?:mpr|k)\d{6}[a-z]?)\.pdf$")
_MINUTES = re.compile(r"/mopo/mpmsche_minu/minu_\d{4}/(g\d{6})\.(?:pdf|htm)$")
# Lines of a page that are not the text: the Bank's name on its own, the PDF
# link, the date line and the heading that repeats the title.
_BANK_NAME = chr(0x65E5) + chr(0x672C) + chr(0x9280) + chr(0x884C)
_PDF_LINK = "PDF"
_OPINIONS = re.compile(r"/mopo/mpmsche_minu/opinion_\d{4}/(opi\d{6})\.pdf$")
_OUTLOOK = re.compile(r"/mopo/outlook/(gor(\d{2})(\d{2})a)\.pdf$")
_SPEECH_PAGE = re.compile(r"/about/press/koen_\d{4}/(ko\d{6}[a-z])\.htm$")
_SPEECH_PDF = re.compile(r"href=\"(/about/press/koen_\d{4}/data/ko\d{6}[a-z]\d?\.pdf)\"")
_SPEECH_ROW = re.compile(
    r"<td>([^<]*)</td>\s*<td><a href=\"(/about/press/koen_\d{4}/ko\d{6}[a-z]\.htm)\""
    r"[^>]*>(.*?)</a>",
    re.S,
)


@dataclass(frozen=True, slots=True)
class Item:
    kind: str
    stem: str
    url: str
    title: str
    published: date | None
    meeting: str | None = None  # meeting dates text for minutes and opinions
    speaker_text: str | None = None
    page_url: str | None = None


@dataclass(frozen=True, slots=True)
class Outcome:
    item: Item
    document_id: int
    segments: int
    changed: bool


def _text(markup: str) -> str:
    return clean_title(html.unescape(_TAGS.sub("", markup)))


def list_releases(fetcher: Fetcher, *, since: int) -> list[Item]:
    """MPM statements (``statement``) and other releases (``release``)."""
    items: list[Item] = []
    for year in range(since, datetime.now(UTC).year + 1):
        try:
            page = fetcher.get(RELEASES_INDEX_URL.format(year=year)).text
        except FetchError as exc:
            LOG.warning("no releases index for %s: %s", year, exc)
            continue
        for posted_text, path, title_html in _DATED_ROW.findall(page):
            match = _RELEASE.search(path) or _STATEMENT_PAGE.search(path)
            if match is None:
                continue
            is_statement = _STATEMENT.search(path) or _STATEMENT_PAGE.search(path)
            kind = "statement" if is_statement else "release"
            items.append(
                Item(
                    kind,
                    match.group(1),
                    BASE_URL + path,
                    _text(title_html),
                    parse_ja_date(html.unescape(posted_text)),
                )
            )
    return items


def list_minutes_and_opinions(fetcher: Fetcher, *, since: int) -> list[Item]:
    """Minutes and Summaries of Opinions from their yearly indexes. Each row
    carries the publication date and the meeting dates the document covers."""
    items: list[Item] = []
    sources = (
        ("minutes", MINUTES_INDEX_URL, _MINUTES),
        ("opinions", OPINIONS_INDEX_URL, _OPINIONS),
    )
    for year in range(since, datetime.now(UTC).year + 1):
        for kind, template, pattern in sources:
            try:
                page = fetcher.get(template.format(year=year)).text
            except FetchError as exc:
                LOG.warning("no %s index for %s: %s", kind, year, exc)
                continue
            for posted_text, path, meeting_html in _DATED_ROW.findall(page):
                match = pattern.search(path)
                if match is None:
                    continue
                items.append(
                    Item(
                        kind,
                        match.group(1),
                        BASE_URL + path,
                        "",
                        parse_ja_date(html.unescape(posted_text)),
                        meeting=_text(meeting_html),
                    )
                )
    return items


def list_outlook(fetcher: Fetcher, *, since: int) -> list[Item]:
    page = fetcher.get(OUTLOOK_INDEX_URL).text
    items: list[Item] = []
    for path, text in _CELL_LINK.findall(page) + re.findall(
        r"<a href=\"([^\"]+)\"[^>]*>(.*?)</a>", page, re.S
    ):
        match = _OUTLOOK.search(path)
        if match is None:
            continue
        year = 2000 + int(match.group(2))
        if year < since:
            continue
        items.append(Item("outlook", match.group(1), BASE_URL + path, _text(text), None))
    unique = {item.stem: item for item in items}
    return sorted(unique.values(), key=lambda i: i.stem)


def list_speeches(fetcher: Fetcher, *, since: int) -> list[Item]:
    items: list[Item] = []
    for year in range(since, datetime.now(UTC).year + 1):
        try:
            page = fetcher.get(SPEECHES_INDEX_URL.format(year=year)).text
        except FetchError as exc:
            LOG.warning("no speeches index for %s: %s", year, exc)
            continue
        for speaker_text, path, title_html in _SPEECH_ROW.findall(page):
            match = _SPEECH_PAGE.search(path)
            if match is None:
                continue
            items.append(
                Item(
                    "speech",
                    match.group(1),
                    BASE_URL + path,
                    _text(title_html),
                    None,
                    speaker_text=_text(speaker_text),
                    page_url=BASE_URL + path,
                )
            )
    return items


def speech_pdf_url(page_markup: str) -> str | None:
    """The full-text PDF linked from a speech page (the first PDF; charts follow)."""
    match = _SPEECH_PDF.search(page_markup)
    return BASE_URL + match.group(1) if match else None


def html_body(page_markup: str) -> str:
    """Article paragraphs of a page, one per line, normalised."""
    window = content_window(page_markup, start=["<main"], end=["</main>"])
    return "\n".join(normalise(p) for p in paragraphs(window))


def page_paragraphs(page_markup: str, *, title: str) -> list[str]:
    """The text of a statement or minutes page: its paragraphs without the
    heading, the date line, the Bank's name and the PDF link."""
    heading = title.split(chr(0xFF08))[0]  # the title before its parenthetical
    out: list[str] = []
    for line in html_body(page_markup).split("\n"):
        if not line or line == _BANK_NAME or line.startswith(_PDF_LINK) or _is_date_line(line):
            continue
        if heading and heading in line and len(line) <= len(title) + 24:
            continue  # the page's own heading
        out.append(line)
    return out


def _is_date_line(line: str) -> bool:
    return len(line) <= 16 and parse_ja_date(line) is not None


def _speaker_id(conn: Connection, directory: SpeakerDirectory, text: str, when: date) -> int | None:
    spec = directory.find(INSTITUTION, text, when=when)
    if spec is None:
        LOG.warning("unknown speaker %r", text)
        return None
    return upsert_speaker(
        conn,
        institution=INSTITUTION,
        slug=spec.slug,
        name=spec.name,
        role=spec.role,
        name_ja=spec.name_ja,
    )


def _minutes_title(item: Item, body: str) -> str:
    label = (
        "金融政策決定会合議事要旨" if item.kind == "minutes" else "金融政策決定会合における主な意見"
    )
    return f"{label}（{item.meeting}）" if item.meeting else label or body[:40]


def ingest_item(
    conn: Connection,
    fetcher: Fetcher,
    item: Item,
    *,
    directory: SpeakerDirectory,
    refresh: bool = False,
) -> Outcome:
    pdf_url = item.url
    page_date: date | None = None
    paragraphs: list[str]
    if item.kind == "speech":
        page = fetcher.get(item.url, refresh=refresh).text
        page_date = parse_ja_date(_text(page))
        found = speech_pdf_url(page)
        if found is not None:
            pdf_url = found
            text = pdf_text(fetcher.get(pdf_url, refresh=refresh).content)
            paragraphs = pdf_paragraphs(text)
        else:  # some speeches are published as page text only
            text = html_body(page)
            paragraphs = [p for p in text.split("\n") if p]
    elif item.url.endswith(".htm"):  # statements and minutes are pages since 2018
        page = fetcher.get(item.url, refresh=refresh).text
        text = html_body(page)
        heading = _minutes_title(item, "") if item.kind in ("minutes", "opinions") else item.title
        paragraphs = page_paragraphs(page, title=heading)
    else:
        text = pdf_text(fetcher.get(pdf_url, refresh=refresh).content)
        paragraphs = pdf_paragraphs(text)
    if not paragraphs:
        raise ValueError(f"{pdf_url}: no text found")

    published = item.published or page_date or parse_ja_date(text[:600])
    if published is None:
        raise ValueError(f"{pdf_url}: no publication date found")
    when = datetime(published.year, published.month, published.day, 12, 0, tzinfo=JST)

    if item.kind in ("minutes", "opinions"):
        title = _minutes_title(item, text)
        kind = "minutes" if item.kind == "minutes" else "report"
    elif item.kind == "outlook":
        title = (
            f"経済・物価情勢の展望（{item.title}）基本的見解"
            if item.title
            else "経済・物価情勢の展望"
        )
        kind = "report"
    else:
        title = item.title
        kind = item.kind
    speaker_id = (
        _speaker_id(conn, directory, item.speaker_text, published)
        if item.kind == "speech" and item.speaker_text
        else None
    )
    meta: dict[str, Any] = {}
    if item.url.endswith(".htm") and item.kind != "speech":
        meta["format"] = "html"
    elif pdf_url != item.url or item.kind != "speech":
        meta["pdf_url"] = pdf_url
    if item.kind in ("opinions", "outlook"):
        meta["subkind"] = item.kind
    if item.meeting:
        meta["meeting"] = item.meeting
    document_id = upsert_document(
        conn,
        Document(
            institution=INSTITUTION,
            kind=kind,
            lang="ja",
            external_id=item.stem,
            title=title,
            published_at=when,
            source_url=item.page_url or item.url,
            body="\n\n".join(paragraphs),
            meta=meta,
            speaker_id=speaker_id,
        ),
    )
    segments = [Segment(i, chunk) for i, chunk in enumerate(pack_ja(paragraphs))]
    changed = replace_segments(conn, document_id, segments)
    return Outcome(item, document_id, len(segments), changed)


def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    directory = speakers()
    kinds = set(args.kinds)
    with Fetcher(settings.data_dir / "raw") as fetcher, connect(autocommit=True) as conn:
        items: list[Item] = []
        if kinds & {"statement", "release"}:
            items += [i for i in list_releases(fetcher, since=args.since) if i.kind in kinds]
        if kinds & {"minutes", "opinions"}:
            items += [
                i for i in list_minutes_and_opinions(fetcher, since=args.since) if i.kind in kinds
            ]
        if "outlook" in kinds:
            items += list_outlook(fetcher, since=args.since)
        if "speech" in kinds:
            items += list_speeches(fetcher, since=args.since)
        items.sort(key=lambda i: (i.published or date.min, i.stem))
        if args.limit:
            items = items[-args.limit :]
        failures = 0
        for item in items:
            try:
                outcome = ingest_item(
                    conn, fetcher, item, directory=directory, refresh=args.refresh
                )
            except (FetchError, ValueError) as exc:
                failures += 1
                LOG.warning("%s skipped: %s", item.stem, exc)
                continue
            state = "updated" if outcome.changed else "unchanged"
            sys.stdout.write(
                f"{item.kind:9s}  {item.stem:10s}  doc {outcome.document_id}  "
                f"{outcome.segments:3d} segments  {state}\n"
            )
    return 1 if failures else 0


def add_cli(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "boj-releases", help="Bank of Japan statements, minutes, outlook reports and speeches"
    )
    parser.add_argument("--kinds", nargs="+", choices=KINDS, default=list(KINDS))
    parser.add_argument("--since", type=int, default=FIRST_YEAR, help="first year to include")
    parser.add_argument("--limit", type=int, default=0, help="only the N most recent")
    parser.add_argument("--refresh", action="store_true", help="ignore cached pages")
    parser.set_defaults(func=run)
