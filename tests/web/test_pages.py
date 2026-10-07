from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from asknews.answer.llm import ScriptedLLM

pytestmark = pytest.mark.integration
Api = tuple[TestClient, dict[str, int], ScriptedLLM]


def test_home_renders_latest_and_language_switch(api: Api) -> None:
    client, _, _ = api
    page = client.get("/")
    assert page.status_code == 200
    assert "Core archive" in page.text
    assert "Federal Reserve issues FOMC statement" in page.text
    assert '<html lang="en" data-prefix="">' in page.text
    assert 'href="/ja" hreflang="ja"' in page.text  # the switch points at the Japanese home
    japanese = client.get("/ja")
    assert japanese.status_code == 200
    assert "アーカイブ" in japanese.text  # the Japanese nav label for Archive
    assert '<html lang="ja" data-prefix="/ja">' in japanese.text
    assert 'href="/ja/archive"' in japanese.text  # navigation stays in the language
    assert 'href="/" hreflang="en"' in japanese.text


def test_language_versions_link_each_other(api: Api) -> None:
    client, _, _ = api
    page = client.get("/archive/latest", params={"kind": "statement"}).text
    assert 'href="/ja/archive/latest?kind=statement" hreflang="ja"' in page
    assert (
        '<link rel="alternate" hreflang="ja" href="https://asknews.jp/ja/archive/latest?kind=statement">'
        in page
    )
    assert '<link rel="canonical" href="https://asknews.jp/archive/latest">' in page
    japanese = client.get("/ja/archive/latest", params={"kind": "statement"}).text
    assert 'href="/archive/latest?kind=statement" hreflang="en"' in japanese
    assert client.get("/ja/doc/999999").status_code == 404
    assert client.get("/en/archive").status_code == 404  # English lives at the root


def test_archive_pages(api: Api) -> None:
    client, _, _ = api
    assert "Overview" in client.get("/archive").text
    latest = client.get("/archive/latest", params={"kind": "statement"})
    assert "Federal Reserve issues FOMC statement" in latest.text
    assert "Minutes of the Federal" not in latest.text
    assert client.get("/archive/latest", params={"kind": "poem"}).status_code == 404
    videos = client.get("/archive/videos").text
    assert "Jerome H. Powell" in videos
    assert "ジェローム・パウエル" in client.get("/ja/archive/videos").text
    rankings = client.get("/archive/rankings", params={"days": 3650}).text
    assert "Jerome H. Powell" in rankings
    meetings = client.get("/archive/meetings").text
    assert "4 to 4-1/4 percent" in meetings
    assert 'id="m-fed:20250917"' in meetings
    assert "No briefs have been generated yet." in client.get("/archive/briefs").text


def test_document_pages(api: Api) -> None:
    client, ids, _ = api
    presser = client.get(f"/doc/{ids['presser']}").text
    assert 'data-video-id="6379710022112"' in presser
    assert 'data-seek="14"' in presser
    assert "Chair Powell" in presser
    assert "player.js" in presser
    # A merged answer block keeps two grid cells; anchors sit inside the body.
    with client.app.state.runtime.pool.connection() as conn:  # type: ignore[attr-defined]
        from asknews.ingest.store import Segment, replace_segments

        replace_segments(
            conn,
            ids["minutes"],
            [
                Segment(0, "First question?", speaker="Question"),
                Segment(1, "First part of the answer.", speaker="Kazuo Ueda"),
                Segment(2, "Second part of the answer.", speaker="Kazuo Ueda"),
            ],
        )
        conn.execute(
            "INSERT INTO media (document_id, provider, provider_id) VALUES (%s, 'youtube', 'x')",
            (ids["minutes"],),
        )
    grouped = client.get(f"/doc/{ids['minutes']}").text
    answer = re.search(r'<div class="ql a" id="s2">(.*?)</div></div>', grouped, re.S)
    assert answer is not None
    assert answer.group(1).startswith('<span class="qk">')  # the marker is the first cell
    assert '<span id="s3"></span><p>Second part' in answer.group(1)  # the anchor is inside the body
    statement = client.get(f"/doc/{ids['statement']}").text
    assert "Full text" in statement
    assert "player.js" not in statement
    assert "fed:20250917" in statement  # meeting link in the rail
    missing = client.get("/doc/999999")
    assert missing.status_code == 404
    assert "404" in missing.text
    unknown = client.get("/no/such/page")  # raised by the router, not by a route
    assert unknown.status_code == 404
    assert "404" in unknown.text
    assert "<html" in unknown.text


def test_robots_and_sitemap(api: Api) -> None:
    client, ids, _ = api
    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    assert "Sitemap: https://asknews.jp/sitemap.xml" in robots.text
    sitemap = client.get("/sitemap.xml")
    assert sitemap.headers["content-type"].startswith("application/xml")
    assert f"<loc>https://asknews.jp/doc/{ids['presser']}</loc>" in sitemap.text
    assert f"<loc>https://asknews.jp/ja/doc/{ids['presser']}</loc>" in sitemap.text
    assert 'hreflang="ja" href="https://asknews.jp/ja/archive/meetings"' in sitemap.text
    assert "<loc>https://asknews.jp/ja</loc>" in sitemap.text


def test_ask_and_about_pages(api: Api) -> None:
    client, _, _ = api
    ask = client.get("/ask", params={"q": "What did the Fed decide?"})
    assert 'data-question="What did the Fed decide?"' in ask.text
    assert "ask.js" in ask.text
    # The interface strings the script reads must be a JSON script block: a
    # <template> element's text is empty in browsers and the script would die.
    strings = re.search(
        r'<script type="application/json" id="tpl-strings">(.*?)</script>', ask.text, re.S
    )
    assert strings is not None
    assert json.loads(strings.group(1))["questions"]
    about = client.get("/about").text
    assert "Data sources and terms" in about
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/fonts/fonts.css").status_code == 200
