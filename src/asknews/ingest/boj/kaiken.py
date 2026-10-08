"""Bank of Japan press conferences (記者会見要旨).

Regular post-meeting press conferences are HTML pages since 2018 and PDFs
before that; other press conferences (deputy governors, board members) are
PDFs. All share one structure: a title, the conference date, then
question-and-answer blocks introduced by （問） and （答）, or 【問】 and 【答】
with an opening 【冒頭発言】 statement at joint press conferences abroad.
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
    SEGMENT_CHARS,
    clean_title,
    normalise,
    pack_ja,
    parse_ja_date,
    parse_month_day,
    strip_page_numbers,
)
from asknews.ingest.htmltext import content_window, paragraphs
from asknews.ingest.http import INDEX_MAX_AGE, Fetcher, FetchError
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

INDEX_URL = BASE_URL + "/about/press/kaiken_{year}/index.htm"
FIRST_YEAR = 2011
JST = timezone(timedelta(hours=9))
QUESTION_SPEAKER = "Question"

_ROW = re.compile(
    r"<td>([^<]*)</td>\s*<td><a href=\"(/about/press/kaiken_\d{4}/(kk\d{4,6}[a-z]?)\.(htm|pdf))\""
    r"[^>]*>(.*?)</a>",
    re.S,
)
_TAGS = re.compile(r"<[^>]+>")
# （問）/（答） in most summaries; 【問】/【答】 and an opening 【冒頭発言】 in joint
# press conferences held abroad.
_MARKER = re.compile(r"[（(【]\s*(問|答|冒頭発言)\s*[）)】]")
_MONTH_DAY_IN_PAREN = re.compile(r"(?<=[（(・])(\d{1,2}月\d{1,2}日)(?=[）)])")
_GOVERNOR_TITLE = re.compile(r"^総裁")
_ACTING = re.compile(r"([^（(・]+?)副総裁代理")
_HEADER_NOISE = ("日本銀行", "PDF版ダウンロード", "PDF")


@dataclass(frozen=True, slots=True)
class Listing:
    stem: str
    url: str
    is_html: bool
    title: str
    posted: date


@dataclass(frozen=True, slots=True)
class Block:
    kind: str  # "q" or "a"
    text: str


@dataclass(frozen=True, slots=True)
class Outcome:
    stem: str
    document_id: int
    segments: int
    changed: bool


def list_press_conferences(fetcher: Fetcher, *, since: int = FIRST_YEAR) -> list[Listing]:
    out: list[Listing] = []
    for year in range(since, datetime.now(UTC).year + 1):
        try:
            page = fetcher.get(INDEX_URL.format(year=year), max_age=INDEX_MAX_AGE).text
        except FetchError as exc:
            LOG.warning("no press conference index for %s: %s", year, exc)
            continue
        for posted_text, path, stem, ext, title_html in _ROW.findall(page):
            posted = parse_ja_date(html.unescape(posted_text))
            if posted is None:
                continue
            title = clean_title(html.unescape(_TAGS.sub("", title_html)))
            out.append(Listing(stem, BASE_URL + path, ext == "htm", title, posted))
    out.sort(key=lambda item: (item.posted, item.stem))
    return out


def qa_blocks(text: str) -> list[Block]:
    """Split normalised text into alternating question and answer blocks."""
    pieces = _MARKER.split(strip_page_numbers(normalise(text)))
    blocks: list[Block] = []
    for marker, body in zip(pieces[1::2], pieces[2::2], strict=False):
        cleaned = re.sub(r"[ \t]*\n[ \t]*", "", body).strip()  # PDF line wraps
        if cleaned:
            blocks.append(Block("q" if marker == "問" else "a", cleaned))
    return blocks


def html_text(markup: str) -> str:
    """The article text of a press conference page, markers kept on their own lines."""
    window = content_window(markup, start=["<main"], end=["</main>"])
    items = [p for p in paragraphs(window) if p not in _HEADER_NOISE]
    return "\n".join(items)


def event_date(title: str, body: str, posted: date) -> date:
    """The conference date: from the title's ``（M月D日）``, else the body's
    header line, else the posting date."""
    return parse_month_day(title, posted=posted) or parse_ja_date(body[:400]) or posted


def event_title(title: str, when: date) -> str:
    """``総裁定例記者会見（12月19日）`` -> ``総裁定例記者会見（2025年12月19日）``."""
    dated = f"{when.year}年{when.month}月{when.day}日"
    if _MONTH_DAY_IN_PAREN.search(title):
        return _MONTH_DAY_IN_PAREN.sub(dated, title, count=1)
    return f"{title}（{dated}）"


def resolve_speaker(
    conn: Connection, directory: SpeakerDirectory, title: str, when: date
) -> tuple[int | None, str | None]:
    """``(speaker_id, display name)`` for the person holding the conference."""
    acting = _ACTING.search(title)
    if acting:  # a deputy governor standing in for the governor
        spec = directory.find(INSTITUTION, acting.group(1), when=when)
    elif _GOVERNOR_TITLE.match(title):
        spec = directory.head_on(INSTITUTION, when)
    else:
        spec = directory.find(INSTITUTION, title, when=when)
    if spec is None:
        LOG.warning("unknown speaker in title %r", title)
        return None, None
    speaker_id = upsert_speaker(
        conn,
        institution=INSTITUTION,
        slug=spec.slug,
        name=spec.name,
        role=spec.role,
        name_ja=spec.name_ja,
    )
    return speaker_id, spec.name


def build_segments(blocks: list[Block], answerer: str | None) -> list[Segment]:
    segments: list[Segment] = []
    for block in blocks:
        speaker = QUESTION_SPEAKER if block.kind == "q" else answerer
        for chunk in pack_ja([block.text], max_chars=SEGMENT_CHARS):
            segments.append(Segment(len(segments), chunk, speaker=speaker))
    return segments


def ingest_press_conference(
    conn: Connection,
    fetcher: Fetcher,
    listing: Listing,
    *,
    directory: SpeakerDirectory,
    refresh: bool = False,
) -> Outcome:
    fetched = fetcher.get(listing.url, refresh=refresh)
    text = html_text(fetched.text) if listing.is_html else pdf_text(fetched.content)
    blocks = qa_blocks(text)
    if not blocks:
        raise ValueError(f"{listing.url}: no question-and-answer blocks found")
    when = event_date(listing.title, text, listing.posted)
    speaker_id, speaker_name = resolve_speaker(conn, directory, listing.title, when)
    body = "\n\n".join(("（問）" if b.kind == "q" else "（答）") + b.text for b in blocks)
    document_id = upsert_document(
        conn,
        Document(
            institution=INSTITUTION,
            kind="press_conference",
            lang="ja",
            external_id=listing.stem,
            title=event_title(listing.title, when),
            published_at=datetime(when.year, when.month, when.day, 15, 30, tzinfo=JST),
            source_url=listing.url,
            body=body,
            meta={
                "posted": listing.posted.isoformat(),
                "format": "html" if listing.is_html else "pdf",
            },
            speaker_id=speaker_id,
        ),
    )
    changed = replace_segments(conn, document_id, build_segments(blocks, speaker_name))
    return Outcome(listing.stem, document_id, len(blocks), changed)


def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    directory = speakers()
    with Fetcher(settings.data_dir / "raw") as fetcher, connect(autocommit=True) as conn:
        listings = list_press_conferences(fetcher, since=args.since)
        if args.governor_only:
            listings = [item for item in listings if _GOVERNOR_TITLE.match(item.title)]
        if args.limit:
            listings = listings[-args.limit :]
        failures = 0
        for listing in listings:
            try:
                outcome = ingest_press_conference(
                    conn, fetcher, listing, directory=directory, refresh=args.refresh
                )
            except (FetchError, ValueError) as exc:
                failures += 1
                LOG.warning("%s skipped: %s", listing.stem, exc)
                continue
            state = "updated" if outcome.changed else "unchanged"
            sys.stdout.write(
                f"{listing.posted}  {listing.stem:10s}  doc {outcome.document_id}  "
                f"{outcome.segments:3d} blocks  {state}\n"
            )
    return 1 if failures else 0


def add_cli(subparsers: Any) -> None:
    parser = subparsers.add_parser("boj-kaiken", help="Bank of Japan press conference summaries")
    parser.add_argument("--since", type=int, default=FIRST_YEAR, help="first year to include")
    parser.add_argument("--limit", type=int, default=0, help="only the N most recent")
    parser.add_argument("--governor-only", action="store_true", help="skip other board members")
    parser.add_argument("--refresh", action="store_true", help="ignore cached pages")
    parser.set_defaults(func=run)
