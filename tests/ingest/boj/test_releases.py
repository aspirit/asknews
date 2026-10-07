from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from asknews.db.connection import Connection
from asknews.db.migrate import migrate
from asknews.domain_config import load_speakers
from asknews.ingest.boj import pdf_paragraphs, releases
from asknews.ingest.http import Fetcher

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "boj"
REPO_ROOT = Path(__file__).resolve().parents[3]


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


ROUTES = {
    releases.RELEASES_INDEX_URL.format(year=2026): fixture("mpr_2026_index.html"),
    releases.SCHEDULE_INDEX_URL.format(year=2025): fixture("minu_2025_index.html"),
    releases.OUTLOOK_INDEX_URL: fixture("outlook_index.html"),
    releases.SPEECHES_INDEX_URL.format(year=2026): fixture("koen_2026_index.html"),
    "https://www.boj.or.jp/about/press/koen_2026/ko261006a.htm": fixture("ko261006a.html"),
}
PDF_URLS = {
    "https://www.boj.or.jp/mopo/mpmdeci/mpr_2026/k260918a.pdf",
    "https://www.boj.or.jp/about/press/koen_2026/data/ko261006a.pdf",
}
FAKE_PDF_TEXT = (
    "２０２６年９月１８日\n日本銀行\n\n金融市場調節方針の変更について\n\n"
    "１．日本銀行は、本日、政策\n金利を１．２５％程度とすることを決定した。\n\n２．次の段落。"
)


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url in PDF_URLS:
        return httpx.Response(
            200, content=b"%PDF-1.4 fake", headers={"content-type": "application/pdf"}
        )
    body = ROUTES.get(url)
    if body is None:
        return httpx.Response(404)
    return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})


@pytest.fixture
def fetcher(tmp_path: Path) -> Fetcher:
    return Fetcher(
        tmp_path, transport=httpx.MockTransport(handler), min_interval=0, sleep=lambda _s: None
    )


def test_release_index_distinguishes_statements(fetcher: Fetcher) -> None:
    items = releases.list_releases(fetcher, since=2026)
    kinds = {i.stem: i.kind for i in items}
    assert kinds == {"mpr260930a": "release", "k260918b": "release", "k260918a": "statement"}
    statement = next(i for i in items if i.kind == "statement")
    assert statement.title == "金融市場調節方針の変更について"
    assert statement.published == date(2026, 9, 18)


def test_schedule_yields_minutes_and_opinions_with_year_rollover(fetcher: Fetcher) -> None:
    items = releases.list_schedule(fetcher, since=2025)
    by_stem = {i.stem: i for i in items}
    assert by_stem["g251219"].published == date(2026, 1, 27)  # December meeting, January minutes
    assert by_stem["opi251219"].published == date(2025, 12, 26)
    assert by_stem["g250501"].published == date(2025, 6, 20)
    assert by_stem["g251219"].meeting == "12月18日（木）・19日（金）"


def test_outlook_lists_basic_view_only(fetcher: Fetcher) -> None:
    items = releases.list_outlook(fetcher, since=2026)
    assert [(i.stem, i.title) for i in items] == [("gor2607a", "2026年7月（基本的見解）")]


def test_speeches_carry_speaker_text(fetcher: Fetcher) -> None:
    items = releases.list_speeches(fetcher, since=2026)
    assert [(i.stem, i.speaker_text) for i in items] == [
        ("ko261006a", "植田総裁"),
        ("ko260902a", "高田審議委員"),
    ]


def test_pdf_paragraphs_join_wrapped_lines() -> None:
    assert (
        pdf_paragraphs(FAKE_PDF_TEXT)[2]
        == "1．日本銀行は、本日、政策金利を1.25%程度とすることを決定した。"
    )


@pytest.mark.integration
def test_ingest_statement_and_speech(
    db: Connection, fetcher: Fetcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate(db)
    monkeypatch.setattr(releases, "pdf_text", lambda _data: FAKE_PDF_TEXT)
    directory = load_speakers(REPO_ROOT / "domain" / "speakers.yaml")
    statement = next(
        i for i in releases.list_releases(fetcher, since=2026) if i.kind == "statement"
    )
    speech = releases.list_speeches(fetcher, since=2026)[0]
    first = releases.ingest_item(db, fetcher, statement, directory=directory)
    second = releases.ingest_item(db, fetcher, speech, directory=directory)
    rows = {
        r["kind"]: r
        for r in db.execute(
            "SELECT d.kind, d.title, d.published_at, d.source_url, d.meta, s.slug "
            "FROM documents d LEFT JOIN speakers s ON s.id = d.speaker_id"
        ).fetchall()
    }
    assert rows["statement"]["title"] == "金融市場調節方針の変更について"
    assert rows["statement"]["published_at"].date() == date(2026, 9, 18)
    assert rows["speech"]["slug"] == "ueda"
    assert rows["speech"]["published_at"].date() == date(2026, 10, 6)
    assert rows["speech"]["source_url"].endswith("ko261006a.htm")
    assert rows["speech"]["meta"]["pdf_url"].endswith("ko261006a.pdf")
    assert first.segments >= 1
    assert second.segments >= 1
