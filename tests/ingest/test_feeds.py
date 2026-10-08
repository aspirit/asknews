from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from asknews.db.connection import Connection
from asknews.db.migrate import migrate
from asknews.ingest import feeds
from asknews.ingest.http import Fetcher
from tests.ingest.pdfs import minimal_pdf

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "feeds"
REPO_ROOT = Path(__file__).resolve().parents[2]


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


def test_rss2_entries_with_cdata_links_and_dates() -> None:
    entries = feeds.parse_feed(fixture("rss2.xml"))
    assert [e.link for e in entries] == [
        "https://example.test/press/pr1.en.html",
        "https://example.test/jgbs/auction/result1.htm",
    ]
    assert entries[0].published == datetime(2026, 10, 6, 14, 45, tzinfo=UTC)
    assert entries[0].description == "Rates were held."
    assert entries[1].published is not None
    assert entries[1].published.utcoffset() is not None


def test_rdf_entries_carry_creator() -> None:
    entries = feeds.parse_feed(fixture("rdf.xml"))
    assert entries[0].creator == "A. Example"
    assert entries[0].published == datetime(2026, 10, 7, tzinfo=UTC)


def test_atom_entries_expose_youtube_extensions() -> None:
    entries = feeds.parse_feed(fixture("atom.xml"))
    assert entries[0].link == "https://www.youtube.com/watch?v=abc123"
    assert entries[0].extra["videoId"] == "abc123"


def test_source_filters() -> None:
    source = feeds.Source(
        "x", "mof", "u", "ja", "release", "page", include=("/policy/",), exclude=("/auction/",)
    )
    assert source.accepts("https://example.test/policy/a.htm")
    assert not source.accepts("https://example.test/policy/auction/a.htm")
    assert not source.accepts("https://example.test/other/a.htm")


def test_title_strip_removes_category_and_stock_phrase() -> None:
    source = feeds.Source(
        "fsa",
        "fsa",
        "u",
        "ja",
        "release",
        "page",
        title_strip=("^[^,]{1,8},", "について(?:掲載|公表)しました。$"),
    )
    title = "記者会見,片山財務大臣閣議後記者会見の概要（令和８年９月29日）について掲載しました。"
    assert feeds.clean_title(title, source) == "片山財務大臣閣議後記者会見の概要（令和8年9月29日）"
    assert feeds.clean_title(
        "Plain title", feeds.Source("x", "ecb", "u", "en", "release", "page")
    ) == ("Plain title")


def test_event_date_comes_from_the_text_not_the_posting_date() -> None:
    posted = date(2026, 10, 7)
    assert feeds.event_date(
        "片山財務大臣閣議後記者会見の概要（令和8年9月25日）", posted=posted
    ) == date(2026, 9, 25)
    assert feeds.event_date("会見の概要（2026年9月29日）", posted=posted) == date(2026, 9, 29)
    assert feeds.event_date(
        "Speech by Ms Jane Doe at the forum, 1 October 2026", posted=posted
    ) == date(2026, 10, 1)
    assert feeds.event_date("Remarks, Washington, D.C., October 2, 2026.", posted=posted) == date(
        2026, 10, 2
    )
    assert (
        feeds.event_date("A survey covering 2024年3月15日 results", posted=posted) is None
    )  # too old
    assert feeds.event_date("Minutes dated 15 October 2026", posted=posted) is None  # in the future
    assert feeds.event_date("No date here", posted=posted) is None
    sources = feeds.load_sources(REPO_ROOT / "domain" / "feeds.yaml")
    codes = {s.code for s in sources}
    assert {"ecb-press", "bis-speeches", "bea-news", "mof-news", "fsa-news", "boe-news"} <= codes
    boe = next(s for s in sources if s.code == "boe-news")
    assert boe.body == "none"


PAGE = (
    "<html><body><nav>menu</nav><main><h1>Monetary policy decision</h1>"
    "<p>PRESS RELEASE</p><p>The Governing Council decided to keep the three key interest rates "
    "unchanged. Inflation is expected to settle at target over the medium term, and the Council "
    "will continue to follow a data-dependent approach to determining the appropriate stance.</p>"
    "<p>Reproduction is permitted provided the source is acknowledged.</p></main></body></html>"
)


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url == "https://example.test/press/pr1.en.html":
        return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})
    if url == "https://example.test/press/speech.pdf":
        return httpx.Response(
            200, content=minimal_pdf(PDF_LINES), headers={"content-type": "application/pdf"}
        )
    if url == "https://example.test/press/mislabelled":  # a PDF served as HTML
        return httpx.Response(
            200, content=minimal_pdf(PDF_LINES), headers={"content-type": "text/html"}
        )
    if url == "https://example.test/press/scan.pdf":  # a file without a text layer
        return httpx.Response(
            200,
            content=b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj << >> endobj",
            headers={"content-type": "application/pdf"},
        )
    return httpx.Response(404)


PDF_LINES = [
    "The Governing Council decided to keep the three key interest rates unchanged.",
    "Inflation is expected to settle at around the two percent target over the medium term.",
    "The Council will follow a data-dependent and meeting-by-meeting approach.",
]


def test_linked_pdfs_are_read_as_pdfs(tmp_path: Path) -> None:
    fetcher = Fetcher(tmp_path, transport=httpx.MockTransport(handler), min_interval=0)
    source = feeds.Source("ecb-press", "ecb", "u", "en", "release", "page")
    posted = datetime(2026, 10, 7, tzinfo=UTC)
    for link in ("https://example.test/press/speech.pdf", "https://example.test/press/mislabelled"):
        entry = feeds.Entry("A speech", link, posted, description="Blurb only.")
        items, origin = feeds.body_paragraphs(fetcher, source, entry, refresh=False)
        assert origin == "page", link
        assert any("three key interest rates unchanged" in p for p in items), link
        assert not any(p.startswith("%PDF") for p in items), link
    # No text layer: the feed's own blurb stands in, never the file's bytes.
    entry = feeds.Entry(
        "A scan", "https://example.test/press/scan.pdf", posted, description="Blurb only."
    )
    assert feeds.body_paragraphs(fetcher, source, entry, refresh=False) == (
        ["Blurb only."],
        "description",
    )
    # A missing page falls back the same way.
    entry = feeds.Entry(
        "Gone", "https://example.test/press/gone.html", posted, description="Blurb only."
    )
    assert feeds.body_paragraphs(fetcher, source, entry, refresh=False) == (
        ["Blurb only."],
        "description",
    )
    fetcher.close()


@pytest.mark.integration
def test_ingest_entry_from_page_and_title_only(db: Connection, tmp_path: Path) -> None:
    migrate(db)
    fetcher = Fetcher(tmp_path, transport=httpx.MockTransport(handler), min_interval=0)
    entries = feeds.parse_feed(fixture("rss2.xml"))
    ecb = feeds.Source(
        "ecb-press",
        "ecb",
        "u",
        "en",
        "release",
        "page",
        main=("<main", "</main>"),
        drop=("PRESS RELEASE", "Reproduction is permitted"),
    )
    outcome = feeds.ingest_entry(db, fetcher, ecb, entries[0])
    row = db.execute(
        "SELECT body, title FROM documents WHERE id = %s", (outcome.document_id,)
    ).fetchone()
    assert row is not None
    assert row["body"].startswith("Monetary policy decision\n\nThe Governing Council")
    assert "PRESS RELEASE" not in row["body"]
    assert outcome.segments == 1

    boe = feeds.Source("boe-news", "boe", "u", "en", "release", "none")
    titled = feeds.ingest_entry(db, fetcher, boe, entries[1])
    row = db.execute(
        "SELECT body, meta FROM documents WHERE id = %s", (titled.document_id,)
    ).fetchone()
    assert row is not None
    assert (row["body"], row["meta"]["text"], titled.segments) == (None, "title_only", 0)

    bis = feeds.Source(
        "bis-speeches", "bis", "u", "en", "speech", "description", event="description"
    )
    blurb = feeds.Entry(
        "Monetary policy in a world of shocks",
        "https://example.test/review/r261007a.htm",
        datetime(2026, 10, 7, tzinfo=UTC),
        description="Speech by Ms Jane Doe at the forum, Frankfurt, 1 October 2026.",
    )
    described = feeds.ingest_entry(db, fetcher, bis, blurb)
    row = db.execute(
        "SELECT published_at, meta FROM documents WHERE id = %s", (described.document_id,)
    ).fetchone()
    assert row is not None
    assert row["meta"]["text"] == "description"  # never summarised as if it were the speech
    assert row["published_at"].date() == date(2026, 10, 7)  # when the library posted it
    assert row["meta"]["event_date"] == "2026-10-01"  # the day of the speech, kept beside it

    # A body that mentions an earlier decision does not re-date a release.
    ecb_entry = feeds.Entry(
        "Monetary policy decisions",
        "https://example.test/press/2026/html/decisions.en.html",
        datetime(2026, 10, 8, tzinfo=UTC),
        description="Following our decision of September 16, 2026, the Governing Council today...",
    )
    undated_rule = feeds.ingest_entry(db, fetcher, bis, ecb_entry)
    row = db.execute(
        "SELECT published_at, meta FROM documents WHERE id = %s", (undated_rule.document_id,)
    ).fetchone()
    assert row is not None
    assert row["published_at"].date() == date(2026, 10, 8)
    assert row["meta"]["event_date"] == "2026-09-16"  # named in the designated field
    plain = feeds.Source("ecb-press", "ecb", "u", "en", "release", "description")
    kept = feeds.ingest_entry(db, fetcher, plain, ecb_entry)
    row = db.execute("SELECT meta FROM documents WHERE id = %s", (kept.document_id,)).fetchone()
    assert row is not None
    assert "event_date" not in row["meta"]  # no rule for this source: the body is not a date
