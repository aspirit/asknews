"""FOMC press conferences: video, captions and transcript.

Each press conference page embeds the Board's own video player and links the
transcript PDF. The player's public playback API returns the video metadata
together with a caption track whose text is the transcript itself, speaker
labels included, so segments with timestamps come straight from the captions.
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
from asknews.ingest.fed.captions import repair_cues
from asknews.ingest.http import INDEX_MAX_AGE, Fetched, Fetcher, FetchError
from asknews.ingest.pdf import pdf_text
from asknews.ingest.store import (
    Document,
    Media,
    Segment,
    replace_segments,
    upsert_document,
    upsert_media,
    upsert_speaker,
)
from asknews.ingest.turns import segment_cues, window_text
from asknews.ingest.vtt import parse_vtt
from asknews.settings import get_settings

LOG = logging.getLogger(__name__)

CALENDARS_URL = f"{BASE_URL}/monetarypolicy/fomccalendars.htm"
HISTORICAL_URL = BASE_URL + "/monetarypolicy/fomchistorical{year}.htm"
PAGE_URL = BASE_URL + "/monetarypolicy/fomcpresconf{date}.htm"
PLAYER_URL = "https://players.brightcove.net/{account}/default_default/index.min.js"
PLAYBACK_URL = "https://edge.api.brightcove.com/playback/v1/accounts/{account}/videos/{video_id}"
FIRST_YEAR = 2011  # the first FOMC press conference was held in April 2011

# One page is linked under a misspelt name on the Board's site ("pressconf"), so
# the link is kept as found instead of being rebuilt from the date.
_PAGE_LINK = re.compile(r"(/monetarypolicy/fomcpress?conf(\d{8})\.htm)")
_VIDEO_TAG = re.compile(r"<video\b[^>]*>", re.IGNORECASE)
_ATTR = re.compile(r'(data-video-id|data-account)="(\d+)"')
_TRANSCRIPT = re.compile(r'href="(/mediacenter/files/FOMCpresconf\d{8}\.pdf)"')
_POLICY_KEY = re.compile(r'policyKey:"([^"]+)"')
_APOSTROPHES = "['" + chr(0x2019) + "]"  # straight or typographic
_PRINCIPAL = re.compile(
    r"\b(Chair(?:man|woman)?|Vice Chair(?:man|woman)?)\s+([A-Z][A-Za-z\-]+)"
    + _APOSTROPHES
    + r"s Press Conference"
)
_LABEL = re.compile(r"^(Chair(?:man|woman)?|Vice Chair(?:man|woman)?)\s+([A-Z][A-Za-z\-]+)$")


class PageError(ValueError):
    """A press conference page did not contain what we need."""


@dataclass(frozen=True, slots=True)
class PageInfo:
    video_id: str
    account: str
    transcript_url: str | None


@dataclass(frozen=True, slots=True)
class Video:
    video_id: str
    name: str | None
    duration_sec: int | None
    published_at: datetime | None
    captions_url: str | None
    poster: str | None


@dataclass(frozen=True, slots=True)
class Outcome:
    date: str
    document_id: int
    segments: int
    changed: bool
    timestamped: bool


def list_pages(fetcher: Fetcher, *, since: int = FIRST_YEAR) -> dict[str, str]:
    """Press conference page URLs by date (``YYYYMMDD``), oldest first.

    The calendars page lists recent years; older years live on one
    historical page per year.
    """
    pages: dict[str, str] = {}

    def collect(markup: str) -> None:
        for path, date in _PAGE_LINK.findall(markup):
            pages.setdefault(date, BASE_URL + path)

    collect(fetcher.get(CALENDARS_URL, max_age=INDEX_MAX_AGE).text)
    earliest_listed = min((int(d[:4]) for d in pages), default=datetime.now(UTC).year)
    for year in range(since, earliest_listed):
        try:
            collect(fetcher.get(HISTORICAL_URL.format(year=year), max_age=INDEX_MAX_AGE).text)
        except FetchError as exc:
            LOG.warning("no historical page for %s: %s", year, exc)
    return {date: pages[date] for date in sorted(pages) if int(date[:4]) >= since}


def list_dates(fetcher: Fetcher, *, since: int = FIRST_YEAR) -> list[str]:
    return list(list_pages(fetcher, since=since))


def read_page(html: str) -> PageInfo:
    tag = _VIDEO_TAG.search(html)
    if tag is None:
        raise PageError("no video player on page")
    attrs = dict(_ATTR.findall(tag.group(0)))
    if "data-video-id" not in attrs or "data-account" not in attrs:
        raise PageError("video player without id or account")
    transcript = _TRANSCRIPT.search(html)
    return PageInfo(
        video_id=attrs["data-video-id"],
        account=attrs["data-account"],
        transcript_url=BASE_URL + transcript.group(1) if transcript else None,
    )


def policy_key(fetcher: Fetcher, account: str) -> str:
    match = _POLICY_KEY.search(fetcher.get(PLAYER_URL.format(account=account)).text)
    if match is None:
        raise PageError(f"no policy key in player for account {account}")
    return match.group(1)


def read_video(payload: dict[str, Any]) -> Video:
    captions = next(
        (
            track.get("src")
            for track in payload.get("text_tracks") or []
            if track.get("kind") == "captions" and (track.get("srclang") or "en").startswith("en")
        ),
        None,
    )
    published = payload.get("published_at")
    duration_ms = payload.get("duration")
    return Video(
        video_id=str(payload.get("id") or ""),
        name=payload.get("name"),
        duration_sec=round(duration_ms / 1000) if isinstance(duration_ms, int | float) else None,
        published_at=datetime.fromisoformat(str(published).replace("Z", "+00:00"))
        if published
        else None,
        captions_url=str(captions) if captions else None,
        poster=payload.get("poster"),
    )


def principal_speaker(text: str) -> tuple[str | None, str | None]:
    """``(role, surname)`` from a transcript title such as
    ``Transcript of Chair Powell's Press Conference``."""
    match = _PRINCIPAL.search(text)
    return (match.group(1), match.group(2)) if match else (None, None)


def _video(fetcher: Fetcher, page: PageInfo, *, refresh: bool) -> tuple[Video, Fetched | None]:
    """Video metadata plus its caption file, refreshing once if the signed URL expired."""
    key = policy_key(fetcher, page.account)
    url = PLAYBACK_URL.format(account=page.account, video_id=page.video_id)
    headers = {"Accept": f"application/json;pk={key}"}
    for attempt in range(2):
        payload = fetcher.get(url, headers=headers, refresh=refresh or attempt > 0)
        video = read_video(payload.json_body())
        if video.captions_url is None:
            return video, None
        try:
            return video, fetcher.get(video.captions_url, refresh=attempt > 0)
        except FetchError as exc:
            if exc.status not in (400, 403) or attempt > 0:
                raise
            LOG.info("caption URL for %s expired; refreshing metadata", page.video_id)
    raise AssertionError("unreachable")


def _speaker_id(
    conn: Connection,
    directory: SpeakerDirectory,
    *,
    head: str,
    segments: list[Segment],
    when: datetime,
) -> int | None:
    """Resolve the principal speaker: transcript title, else the first speaker
    label in the captions, else whoever headed the institution that day."""
    role, surname = principal_speaker(head)
    if surname is None:
        label = next((s.speaker for s in segments if s.speaker), None)
        match = _LABEL.match(label) if label else None
        if match:
            role, surname = match.group(1), match.group(2)
    spec = directory.find(INSTITUTION, surname) if surname else None
    if spec is None and surname is None:
        spec = directory.head_on(INSTITUTION, when.date())
    if spec is not None:
        return upsert_speaker(
            conn,
            institution=INSTITUTION,
            slug=spec.slug,
            name=spec.name,
            role=spec.role,
            name_ja=spec.name_ja,
        )
    if surname is None:
        return None
    slug = re.sub(r"[^a-z]", "", surname.lower())
    return upsert_speaker(conn, institution=INSTITUTION, slug=slug, name=surname, role=role)


def _transcript(fetcher: Fetcher, page: PageInfo) -> str | None:
    """The transcript PDF's text, used to repair the captions; absent when not published."""
    if page.transcript_url is None:
        return None
    try:
        return pdf_text(fetcher.get(page.transcript_url).content)
    except FetchError as exc:
        LOG.warning("transcript unavailable, captions kept as published: %s", exc)
        return None


def _statement_body(conn: Connection, date: str) -> str | None:
    """The FOMC statement of the same meeting, when it is already in the archive."""
    row = conn.execute(
        """
        SELECT d.body FROM documents d JOIN institutions i ON i.id = d.institution_id
         WHERE i.code = %s AND d.kind = 'statement' AND d.external_id = %s
        """,
        (INSTITUTION, date),
    ).fetchone()
    return str(row["body"]) if row and row["body"] else None


def _title(video: Video, date: str) -> str:
    if video.name:
        return video.name
    when = datetime.strptime(date, "%Y%m%d").replace(tzinfo=UTC)
    return f"FOMC Press Conference {when:%B} {when.day}, {when.year}"


def ingest_press_conference(
    conn: Connection,
    fetcher: Fetcher,
    date: str,
    *,
    directory: SpeakerDirectory,
    refresh: bool = False,
    page_url: str | None = None,
) -> Outcome:
    page_url = page_url or PAGE_URL.format(date=date)
    page = read_page(fetcher.get(page_url, refresh=refresh).text)
    if page.transcript_url is None and not refresh:
        # The transcript PDF is linked days after the event; a cached page
        # from before then would hide it for good, so look once more.
        page = read_page(fetcher.get(page_url, refresh=True).text)
    video, captions = _video(fetcher, page, refresh=refresh)

    if captions is not None:
        transcript = _transcript(fetcher, page)
        statement = None if transcript else _statement_body(conn, date)
        cues = repair_cues(parse_vtt(captions.text), transcript, statement)
        segments = segment_cues(cues)
        body = " ".join(c.text for c in cues)
        timestamped = True
    elif page.transcript_url is not None:
        body = pdf_text(fetcher.get(page.transcript_url).content)
        segments = [Segment(i, text) for i, text in enumerate(window_text(body))]
        timestamped = False
    else:
        raise PageError(f"{date}: neither captions nor transcript available")

    published_at = video.published_at or datetime.strptime(date, "%Y%m%d").replace(
        hour=18, minute=30, tzinfo=UTC
    )
    document_id = upsert_document(
        conn,
        Document(
            institution=INSTITUTION,
            kind="press_conference",
            lang="en",
            external_id=date,
            title=_title(video, date),
            published_at=published_at,
            source_url=page_url,
            body=body,
            meta={
                "transcript_url": page.transcript_url,
                "poster": video.poster,
                "timestamped": timestamped,
            },
            speaker_id=_speaker_id(
                conn, directory, head=body[:400], segments=segments, when=published_at
            ),
        ),
    )
    upsert_media(conn, document_id, Media("brightcove", page.video_id, video.duration_sec))
    changed = replace_segments(conn, document_id, segments)
    return Outcome(date, document_id, len(segments), changed, timestamped)


def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    directory = speakers()
    with (
        Fetcher(settings.data_dir / "raw") as fetcher,
        connect(autocommit=True) as conn,
    ):
        pages = list_pages(fetcher, since=args.since)
        dates = args.dates or list(pages)
        if args.limit:
            dates = dates[-args.limit :]
        failures = 0
        for date in dates:
            try:
                outcome = ingest_press_conference(
                    conn,
                    fetcher,
                    date,
                    directory=directory,
                    refresh=args.refresh,
                    page_url=pages.get(date),
                )
            except (FetchError, PageError) as exc:
                failures += 1
                LOG.warning("%s skipped: %s", date, exc)
                continue
            state = "updated" if outcome.changed else "unchanged"
            stamps = "timestamped" if outcome.timestamped else "no timestamps"
            sys.stdout.write(
                f"{date}  doc {outcome.document_id}  {outcome.segments:4d} segments  "
                f"{stamps}  {state}\n"
            )
    return 1 if failures else 0


def add_cli(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "fed-presconf", help="FOMC press conferences: captions, transcript and video metadata"
    )
    parser.add_argument("--since", type=int, default=FIRST_YEAR, help="first year to include")
    parser.add_argument("--limit", type=int, default=0, help="only the N most recent")
    parser.add_argument("--dates", nargs="*", help="specific dates (YYYYMMDD) instead of the index")
    parser.add_argument("--refresh", action="store_true", help="ignore cached pages")
    parser.set_defaults(func=run)
