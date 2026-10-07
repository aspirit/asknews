from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from asknews.db.connection import Connection
from asknews.db.migrate import migrate
from asknews.domain_config import load_speakers
from asknews.ingest.boj import kaiken, youtube
from asknews.ingest.feeds import parse_feed
from asknews.ingest.http import Fetcher

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "boj"
REPO_ROOT = Path(__file__).resolve().parents[3]


def test_duration_parsing() -> None:
    assert youtube.parse_duration("PT1H2M10S") == 3730
    assert youtube.parse_duration("PT45M") == 2700
    assert youtube.parse_duration(None) is None
    assert youtube.parse_duration("1:02:10") is None


def test_feed_videos_keep_press_conferences_only() -> None:
    entries = parse_feed((FIXTURES / "youtube_feed.xml").read_text("utf-8"))
    videos = youtube.videos_from_feed(entries)
    assert [(v.video_id, v.when) for v in videos] == [
        ("aiDtSnc8XiI", date(2025, 12, 19)),
        ("nodate", None),
    ]


ROUTES = {
    kaiken.INDEX_URL.format(year=2025): (FIXTURES / "kaiken_2025_index.html").read_text("utf-8"),
    "https://www.boj.or.jp/about/press/kaiken_2025/kk251222a.htm": (
        FIXTURES / "kk251222a.html"
    ).read_text("utf-8"),
}


def handler(request: httpx.Request) -> httpx.Response:
    body = ROUTES.get(str(request.url))
    return httpx.Response(200, text=body) if body else httpx.Response(404)


@pytest.mark.integration
def test_videos_attach_to_the_press_conference_of_their_date(
    db: Connection, tmp_path: Path
) -> None:
    migrate(db)
    fetcher = Fetcher(tmp_path, transport=httpx.MockTransport(handler), min_interval=0)
    directory = load_speakers(REPO_ROOT / "domain" / "speakers.yaml")
    listing = next(i for i in kaiken.list_press_conferences(fetcher, since=2025) if i.is_html)
    outcome = kaiken.ingest_press_conference(db, fetcher, listing, directory=directory)

    videos = youtube.videos_from_feed(
        parse_feed((FIXTURES / "youtube_feed.xml").read_text("utf-8"))
    )
    attached = youtube.attach_videos(db, videos)
    assert attached.matched == 1
    assert [v.video_id for v in attached.unmatched] == ["nodate"]
    row = db.execute(
        "SELECT provider, provider_id FROM media WHERE document_id = %s", (outcome.document_id,)
    ).fetchone()
    assert row is not None
    assert (row["provider"], row["provider_id"]) == ("youtube", "aiDtSnc8XiI")
