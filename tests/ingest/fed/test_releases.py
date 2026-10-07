from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from asknews.db.connection import Connection
from asknews.db.migrate import migrate
from asknews.domain_config import load_speakers
from asknews.ingest.fed import presconf, releases
from asknews.ingest.http import Fetcher

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "fed"
REPO_ROOT = Path(__file__).resolve().parents[3]


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


ROUTES = {
    presconf.CALENDARS_URL: fixture("calendars.html"),
    presconf.HISTORICAL_URL.format(year=2024): fixture("historical2024.html"),
    releases.SPEECHES_INDEX_URL.format(year=2025): fixture("speeches2025.html"),
    "https://www.federalreserve.gov/newsevents/pressreleases/monetary20250917a.htm": fixture(
        "statement20250917.html"
    ),
    "https://www.federalreserve.gov/monetarypolicy/fomcminutes20250917.htm": fixture(
        "minutes20250917.html"
    ),
    "https://www.federalreserve.gov/monetarypolicy/fomcprojtabl20250917.htm": fixture(
        "projections20250917.html"
    ),
    "https://www.federalreserve.gov/newsevents/speech/miran20251215a.htm": fixture(
        "speech_miran20251215a.html"
    ),
}


def handler(request: httpx.Request) -> httpx.Response:
    body = ROUTES.get(str(request.url))
    if body is None:
        return httpx.Response(404)
    return httpx.Response(200, text=body, headers={"content-type": "text/html"})


@pytest.fixture
def fetcher(tmp_path: Path) -> Fetcher:
    return Fetcher(
        tmp_path, transport=httpx.MockTransport(handler), min_interval=0, sleep=lambda _s: None
    )


def test_calendar_releases_include_minutes_release_dates_and_projections(
    fetcher: Fetcher,
) -> None:
    found = releases.list_calendar_releases(fetcher, since=2025)
    assert [(r.kind, r.date) for r in found] == [
        ("minutes", "20250917"),
        ("projections", "20250917"),
        ("statement", "20250917"),
    ]
    assert found[0].released == "20251008"
    assert found[2].released is None


def test_projection_table_becomes_median_lines() -> None:
    article = releases.read_projections(fixture("projections20250917.html"))
    assert article.title == "Summary of Economic Projections"
    gdp = next(p for p in article.paragraphs if p.startswith("Change in real GDP"))
    assert gdp == "Change in real GDP: 2026 2.3; 2027 2.4; 2028 2.2; 2029 2.1; Longer run 2.0"
    assert not any(p.startswith("June projection") for p in article.paragraphs)


def test_release_date_prefers_calendar_then_page_then_meeting_day() -> None:
    minutes = releases.Release("minutes", "u", "20250917", "20250917", released="20251008")
    assert releases.release_date(minutes, "").date().isoformat() == "2025-10-08"
    unlisted = releases.Release("minutes", "u", "20250917", "20250917")
    page = "<p>Last Update: October 9, 2025</p>"
    assert releases.release_date(unlisted, page).date().isoformat() == "2025-10-09"
    statement = releases.Release("statement", "u", "20250917", "20250917")
    assert releases.release_date(statement, page).isoformat() == "2025-09-17T18:00:00+00:00"


def test_speeches_are_listed_per_year_without_duplicates(fetcher: Fetcher) -> None:
    found = releases.list_speeches(fetcher, since=2025)
    assert [r.external_id for r in found] == ["powell20251203a", "miran20251215a"]
    assert found[1].url.endswith("/newsevents/speech/miran20251215a.htm")


def test_read_article_statement() -> None:
    article = releases.read_article(fixture("statement20250917.html"))
    assert article.title == "Federal Reserve issues FOMC statement"
    assert article.date == "September 17, 2025"
    assert article.speaker is None
    assert article.paragraphs[0].startswith("Recent indicators")
    joined = " ".join(article.paragraphs)
    assert "Share" not in article.paragraphs
    assert "media inquiries" not in joined
    assert "Implementation Note" not in joined
    assert "Last Update" not in joined


def test_read_article_speech_has_speaker_and_location() -> None:
    article = releases.read_article(fixture("speech_miran20251215a.html"))
    assert article.title == "The Inflation Outlook"
    assert article.speaker == "Governor Stephen I. Miran"
    assert article.location is not None
    assert article.location.startswith("At the School")
    assert article.paragraphs[0].startswith("Thank you")
    assert not any(p.startswith("View speech charts") for p in article.paragraphs)
    assert not any("attachment list" in p for p in article.paragraphs)


def test_split_speaker() -> None:
    assert releases.split_speaker("Governor Stephen I. Miran") == ("Governor", "Stephen I. Miran")
    assert releases.split_speaker("Vice Chair for Supervision Michelle W. Bowman") == (
        "Vice Chair for Supervision",
        "Michelle W. Bowman",
    )
    assert releases.split_speaker("Jerome H. Powell") == (None, "Jerome H. Powell")


@pytest.mark.integration
def test_ingest_statement_minutes_and_speech(db: Connection, fetcher: Fetcher) -> None:
    migrate(db)
    directory = load_speakers(REPO_ROOT / "domain" / "speakers.yaml")
    items = releases.list_calendar_releases(fetcher, since=2025) + releases.list_speeches(
        fetcher, since=2025
    )
    outcomes = {}
    for release in items:
        if release.external_id == "powell20251203a":
            continue  # no fixture page
        outcomes[release.kind] = releases.ingest_release(db, fetcher, release, directory=directory)

    rows = {
        r["kind"]: r
        for r in db.execute(
            "SELECT d.kind, d.title, d.external_id, d.published_at, d.meta, s.slug, s.role, "
            "(SELECT count(*) FROM segments WHERE document_id = d.id) AS n "
            "FROM documents d LEFT JOIN speakers s ON s.id = d.speaker_id"
        ).fetchall()
    }
    assert rows["statement"]["title"] == "Federal Reserve issues FOMC statement"
    assert (
        rows["minutes"]["title"]
        == "Minutes of the Federal Open Market Committee, September 16-17, 2025"
    )
    assert rows["minutes"]["published_at"].date().isoformat() == "2025-10-08"
    assert rows["report"]["title"] == "Summary of Economic Projections, September 17, 2025"
    assert rows["report"]["meta"]["subkind"] == "projections"
    assert rows["speech"]["external_id"] == "miran20251215a"
    assert (rows["speech"]["slug"], rows["speech"]["role"]) == ("miran", "Governor")
    assert all(row["n"] >= 1 for row in rows.values())
    assert all(o.changed for o in outcomes.values())
