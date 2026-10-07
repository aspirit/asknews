from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from asknews.db.connection import Connection
from asknews.db.migrate import migrate
from asknews.domain_config import load_speakers
from asknews.ingest.fed import presconf
from asknews.ingest.http import Fetcher

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[3]


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


ROUTES: dict[str, tuple[int, str, str]] = {
    presconf.CALENDARS_URL: (200, "text/html", fixture("fed/calendars.html")),
    presconf.HISTORICAL_URL.format(year=2024): (
        200,
        "text/html",
        fixture("fed/historical2024.html"),
    ),
    presconf.PAGE_URL.format(date="20250917"): (
        200,
        "text/html",
        fixture("fed/presconf20250917.html"),
    ),
    presconf.PLAYER_URL.format(account="66043936001"): (
        200,
        "text/javascript",
        fixture("fed/player.js"),
    ),
    presconf.PLAYBACK_URL.format(account="66043936001", video_id="6379710022112"): (
        200,
        "application/json",
        fixture("fed/video.json"),
    ),
    "https://cdn.test/signed/captions.vtt": (200, "text/vtt", fixture("fed_presconf_captions.vtt")),
}


class Routes:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.caption_failures = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url == "https://cdn.test/signed/captions.vtt" and self.caption_failures > 0:
            self.caption_failures -= 1
            return httpx.Response(400, text="Signed URL doesn't match request")
        if url not in ROUTES:
            return httpx.Response(404)
        status, content_type, body = ROUTES[url]
        return httpx.Response(status, text=body, headers={"content-type": content_type})


@pytest.fixture
def routes() -> Routes:
    return Routes()


@pytest.fixture
def fetcher(tmp_path: Path, routes: Routes) -> Fetcher:
    return Fetcher(
        tmp_path / "cache",
        transport=httpx.MockTransport(routes.handler),
        min_interval=0,
        sleep=lambda _s: None,
    )


def test_list_dates_merges_calendar_and_historical_pages(fetcher: Fetcher) -> None:
    assert presconf.list_dates(fetcher, since=2024) == ["20240918", "20250917", "20260916"]


def test_list_dates_tolerates_missing_historical_year(fetcher: Fetcher) -> None:
    # 2023 has no fixture page: the 404 is logged and skipped.
    assert presconf.list_dates(fetcher, since=2023) == ["20240918", "20250917", "20260916"]


def test_read_page_extracts_player_and_transcript() -> None:
    info = presconf.read_page(fixture("fed/presconf20250917.html"))
    assert info.video_id == "6379710022112"
    assert info.account == "66043936001"
    assert info.transcript_url is not None
    assert info.transcript_url.endswith("/mediacenter/files/FOMCpresconf20250917.pdf")


def test_read_page_without_player_raises() -> None:
    with pytest.raises(presconf.PageError):
        presconf.read_page("<html><body>nothing here</body></html>")


def test_read_video_picks_english_captions() -> None:
    video = presconf.read_video(json.loads(fixture("fed/video.json")))
    assert video.duration_sec == 3132
    assert video.captions_url == "https://cdn.test/signed/captions.vtt"
    assert video.published_at == datetime(2025, 9, 17, 20, 55, 34, 665000, tzinfo=UTC)


def test_principal_speaker_from_transcript_title() -> None:
    assert presconf.principal_speaker("Transcript of Chair Powell's Press Conference") == (
        "Chair",
        "Powell",
    )
    assert presconf.principal_speaker("Press Conference with Chairman Bernanke") == (None, None)


@pytest.mark.integration
def test_ingest_press_conference_end_to_end(
    db: Connection, fetcher: Fetcher, routes: Routes
) -> None:
    migrate(db)
    directory = load_speakers(REPO_ROOT / "domain" / "speakers.yaml")
    outcome = presconf.ingest_press_conference(db, fetcher, "20250917", directory=directory)
    assert outcome.timestamped is True
    assert outcome.changed is True
    assert outcome.segments > 0

    doc = db.execute(
        "SELECT d.title, d.kind, d.lang, d.published_at, d.meta, s.slug AS speaker, m.provider_id, "
        "m.duration_sec FROM documents d LEFT JOIN speakers s ON s.id = d.speaker_id "
        "JOIN media m ON m.document_id = d.id WHERE d.id = %s",
        (outcome.document_id,),
    ).fetchone()
    assert doc is not None
    assert doc["title"] == "FOMC Press Conference September 17, 2025"
    assert (doc["kind"], doc["lang"], doc["speaker"]) == ("press_conference", "en", "powell")
    assert (doc["provider_id"], doc["duration_sec"]) == ("6379710022112", 3132)
    assert doc["meta"]["timestamped"] is True

    segments = db.execute(
        "SELECT speaker, start_sec FROM segments WHERE document_id = %s ORDER BY position",
        (outcome.document_id,),
    ).fetchall()
    assert segments[0]["speaker"] == "Chair Powell"
    assert segments[0]["start_sec"] == pytest.approx(14.526, abs=0.01)

    again = presconf.ingest_press_conference(db, fetcher, "20250917", directory=directory)
    assert (again.document_id, again.changed) == (outcome.document_id, False)


@pytest.mark.integration
def test_expired_caption_url_triggers_one_refresh(
    db: Connection, fetcher: Fetcher, routes: Routes
) -> None:
    migrate(db)
    routes.caption_failures = 1
    directory = load_speakers(REPO_ROOT / "domain" / "speakers.yaml")
    outcome = presconf.ingest_press_conference(db, fetcher, "20250917", directory=directory)
    assert outcome.timestamped is True
    playback_calls = [r for r in routes.requests if "playback/v1" in str(r.url)]
    assert len(playback_calls) == 2


def test_principal_speaker_accepts_typographic_apostrophe() -> None:
    title = "Transcript of Chairman Bernanke" + chr(0x2019) + "s Press Conference"
    assert presconf.principal_speaker(title) == ("Chairman", "Bernanke")


def test_head_of_institution_by_date() -> None:
    from datetime import date

    directory = load_speakers(REPO_ROOT / "domain" / "speakers.yaml")
    assert directory.head_on("fed", date(2014, 3, 19)) is not None
    assert directory.head_on("fed", date(2014, 3, 19)).slug == "yellen"  # type: ignore[union-attr]
    assert directory.head_on("fed", date(2012, 4, 25)).slug == "bernanke"  # type: ignore[union-attr]
    assert directory.head_on("boj", date(2012, 4, 25)) is None
