"""Videos of the Bank of Japan's press conferences.

The Bank streams and archives the Governor's regular press conferences on
its YouTube channel. The channel's official feed lists the latest uploads;
with a YouTube Data API key the whole uploads playlist is read instead.
Videos are matched to press conference documents by the date in the title
and attached as media, never downloaded.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from datetime import date
from typing import Any

from asknews.db.connection import Connection, connect
from asknews.ingest.boj import INSTITUTION, parse_ja_date
from asknews.ingest.feeds import Entry, parse_feed
from asknews.ingest.http import Fetcher, FetchError
from asknews.ingest.store import Media, upsert_media
from asknews.settings import get_settings

LOG = logging.getLogger(__name__)

CHANNEL_ID = "UC32Yu7NyStgmKYsXvYofPvQ"
FEED_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
API_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
UPLOADS_PLAYLIST = "UU" + CHANNEL_ID[2:]
_PRESS_CONFERENCE = re.compile(r"記者会見")
_ISO_DURATION = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


@dataclass(frozen=True, slots=True)
class Video:
    video_id: str
    title: str
    when: date | None
    duration_sec: int | None = None


@dataclass(frozen=True, slots=True)
class Outcome:
    matched: int
    unmatched: list[Video]


def parse_duration(value: str | None) -> int | None:
    """``PT1H2M10S`` -> 3730."""
    match = _ISO_DURATION.fullmatch(value or "")
    if match is None:
        return None
    hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def videos_from_feed(entries: list[Entry]) -> list[Video]:
    return [
        Video(e.extra["videoId"], e.title, parse_ja_date(e.title))
        for e in entries
        if e.extra.get("videoId") and _PRESS_CONFERENCE.search(e.title)
    ]


def videos_from_api(fetcher: Fetcher, api_key: str) -> list[Video]:
    videos: list[Video] = []
    token: str | None = None
    while True:
        url = (
            f"{API_URL}?part=snippet,contentDetails&maxResults=50"
            f"&playlistId={UPLOADS_PLAYLIST}" + (f"&pageToken={token}" if token else "")
        )
        # The key goes in a header: URLs are written to the cache and to error messages.
        headers = {"X-Goog-Api-Key": api_key}
        payload = fetcher.get(url, headers=headers, refresh=True).json_body()
        for item in payload.get("items") or []:
            snippet = item.get("snippet") or {}
            title = str(snippet.get("title") or "")
            video_id = str(((snippet.get("resourceId") or {}).get("videoId")) or "")
            if video_id and _PRESS_CONFERENCE.search(title):
                details = item.get("contentDetails") or {}
                videos.append(
                    Video(
                        video_id,
                        title,
                        parse_ja_date(title),
                        parse_duration(details.get("duration")),
                    )
                )
        token = payload.get("nextPageToken")
        if not token:
            return videos


def attach_videos(conn: Connection, videos: list[Video]) -> Outcome:
    """Attach each video to the Governor's press conference held on its date."""
    matched = 0
    unmatched: list[Video] = []
    for video in videos:
        if video.when is None:
            unmatched.append(video)
            continue
        row = conn.execute(
            """
            SELECT d.id FROM documents d
              JOIN institutions i ON i.id = d.institution_id
             WHERE i.code = %s AND d.kind = 'press_conference'
               AND (d.published_at AT TIME ZONE 'Asia/Tokyo')::date = %s
             ORDER BY d.title LIKE '総裁%%' DESC, d.id
             LIMIT 1
            """,
            (INSTITUTION, video.when),
        ).fetchone()
        if row is None:
            unmatched.append(video)
            continue
        upsert_media(conn, int(row["id"]), Media("youtube", video.video_id, video.duration_sec))
        matched += 1
    return Outcome(matched, unmatched)


def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    with Fetcher(settings.data_dir / "raw") as fetcher, connect(autocommit=True) as conn:
        try:
            if settings.youtube_api_key and not args.feed_only:
                videos = videos_from_api(fetcher, settings.youtube_api_key)
            else:
                videos = videos_from_feed(parse_feed(fetcher.get(FEED_URL, refresh=True).text))
        except FetchError as exc:
            LOG.error("channel unavailable: %s", exc)  # noqa: TRY400 - no traceback needed
            return 1
        outcome = attach_videos(conn, videos)
    sys.stdout.write(f"{len(videos)} press conference videos, {outcome.matched} attached\n")
    for video in outcome.unmatched:
        sys.stdout.write(f"  unmatched: {video.video_id}  {video.title}\n")
    return 0


def add_cli(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "boj-videos", help="attach the Bank of Japan's YouTube press conference videos"
    )
    parser.add_argument("--feed-only", action="store_true", help="ignore the API key if set")
    parser.set_defaults(func=run)
