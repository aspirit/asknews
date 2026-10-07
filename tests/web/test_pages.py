from __future__ import annotations

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
    japanese = client.get("/?lang=ja")
    assert japanese.cookies.get("lang") == "ja"
    assert "アーカイブ" in japanese.text  # the Japanese nav label for Archive
    switched = client.get("/lang/en?next=/archive", follow_redirects=False)
    assert switched.status_code == 303
    assert switched.headers["location"] == "/archive"


def test_archive_pages(api: Api) -> None:
    client, _, _ = api
    assert "Overview" in client.get("/archive").text
    latest = client.get("/archive/latest", params={"kind": "statement"})
    assert "Federal Reserve issues FOMC statement" in latest.text
    assert "Minutes of the Federal" not in latest.text
    assert client.get("/archive/latest", params={"kind": "poem"}).status_code == 404
    videos = client.get("/archive/videos").text
    assert "Jerome H. Powell" in videos
    assert "ジェローム・パウエル" in client.get("/archive/videos", params={"lang": "ja"}).text
    client.cookies.clear()
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
    statement = client.get(f"/doc/{ids['statement']}").text
    assert "Full text" in statement
    assert "player.js" not in statement
    assert "fed:20250917" in statement  # meeting link in the rail
    missing = client.get("/doc/999999")
    assert missing.status_code == 404
    assert "404" in missing.text


def test_ask_and_about_pages(api: Api) -> None:
    client, _, _ = api
    ask = client.get("/ask", params={"q": "What did the Fed decide?"})
    assert 'data-question="What did the Fed decide?"' in ask.text
    assert "ask.js" in ask.text
    about = client.get("/about").text
    assert "Data sources and terms" in about
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/fonts/fonts.css").status_code == 200
