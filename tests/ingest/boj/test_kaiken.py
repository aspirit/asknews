from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from asknews.db.connection import Connection
from asknews.db.migrate import migrate
from asknews.domain_config import load_speakers
from asknews.ingest.boj import kaiken
from asknews.ingest.http import Fetcher

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "boj"
REPO_ROOT = Path(__file__).resolve().parents[3]

ROUTES = {
    kaiken.INDEX_URL.format(year=2025): (FIXTURES / "kaiken_2025_index.html").read_text("utf-8"),
    "https://www.boj.or.jp/about/press/kaiken_2025/kk251222a.htm": (
        FIXTURES / "kk251222a.html"
    ).read_text("utf-8"),
}


def handler(request: httpx.Request) -> httpx.Response:
    body = ROUTES.get(str(request.url))
    if body is None:
        return httpx.Response(404)
    return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})


@pytest.fixture
def fetcher(tmp_path: Path) -> Fetcher:
    return Fetcher(
        tmp_path, transport=httpx.MockTransport(handler), min_interval=0, sleep=lambda _s: None
    )


def test_listing_reads_rows_and_formats(fetcher: Fetcher) -> None:
    items = kaiken.list_press_conferences(fetcher, since=2025)
    assert [(i.stem, i.is_html, i.posted) for i in items] == [
        ("kk251128a", False, date(2025, 11, 28)),
        ("kk251202a", False, date(2025, 12, 2)),
        ("kk251222a", True, date(2025, 12, 22)),
    ]
    assert items[1].title == "総裁記者会見（12月1日）"


def test_qa_blocks_from_html_and_pdf_style_text() -> None:
    text = kaiken.html_text(ROUTES["https://www.boj.or.jp/about/press/kaiken_2025/kk251222a.htm"])
    blocks = kaiken.qa_blocks(text)
    assert [b.kind for b in blocks] == ["q", "a", "q", "a"]
    assert blocks[1].text.startswith("本日の決定会合では")
    pdf_like = "（問）\n一問目は中東情勢に\nついてです。\n\n（答）\nまず、中東\n情勢ですが。"
    pdf_blocks = kaiken.qa_blocks(pdf_like)
    assert [b.text for b in pdf_blocks] == [
        "一問目は中東情勢についてです。",
        "まず、中東情勢ですが。",
    ]


def test_event_date_and_title() -> None:
    when = kaiken.event_date("総裁定例記者会見（12月19日）", "", date(2025, 12, 22))
    assert when == date(2025, 12, 19)
    assert (
        kaiken.event_title("総裁定例記者会見（12月19日）", when)
        == "総裁定例記者会見（2025年12月19日）"
    )
    jan = kaiken.event_date("総裁記者会見（12月26日）", "", date(2026, 1, 6))
    assert jan == date(2025, 12, 26)


def test_speaker_resolution_uses_tenure_for_governor() -> None:
    directory = load_speakers(REPO_ROOT / "domain" / "speakers.yaml")
    assert directory.head_on("boj", date(2025, 12, 19)).slug == "ueda"  # type: ignore[union-attr]
    assert directory.head_on("boj", date(2015, 1, 21)).slug == "kuroda"  # type: ignore[union-attr]
    assert directory.find("boj", "野口審議委員記者会見（11月27日）").slug == "noguchi"  # type: ignore[union-attr]
    assert directory.find("boj", "氷見野副総裁記者会見").slug == "himino"  # type: ignore[union-attr]


@pytest.mark.integration
def test_ingest_html_press_conference(db: Connection, fetcher: Fetcher) -> None:
    migrate(db)
    directory = load_speakers(REPO_ROOT / "domain" / "speakers.yaml")
    listing = next(i for i in kaiken.list_press_conferences(fetcher, since=2025) if i.is_html)
    outcome = kaiken.ingest_press_conference(db, fetcher, listing, directory=directory)
    assert outcome.segments == 4
    row = db.execute(
        "SELECT d.title, d.lang, d.published_at, s.slug FROM documents d "
        "JOIN speakers s ON s.id = d.speaker_id WHERE d.id = %s",
        (outcome.document_id,),
    ).fetchone()
    assert row is not None
    assert row["title"] == "総裁定例記者会見（2025年12月19日）"
    assert (row["lang"], row["slug"]) == ("ja", "ueda")
    assert row["published_at"].date() == date(2025, 12, 19)
    segments = db.execute(
        "SELECT speaker, text FROM segments WHERE document_id = %s ORDER BY position",
        (outcome.document_id,),
    ).fetchall()
    assert [s["speaker"] for s in segments] == ["Question", "Kazuo Ueda", "Question", "Kazuo Ueda"]
    assert "0.5%" in segments[1]["text"]  # full-width percent folded to ASCII
