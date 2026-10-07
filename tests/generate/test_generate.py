from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from asknews.answer.llm import ScriptedLLM
from asknews.archive.meetings import list_meetings
from asknews.generate.briefs import daily_brief, meeting_brief, meetings_without_brief
from asknews.generate.summaries import summarize_pending

pytestmark = pytest.mark.integration
Api = tuple[TestClient, dict[str, int], ScriptedLLM]

SUMMARY_REPLY = (
    '{"summary": "The Committee lowered the target range by a quarter point.",'
    ' "title_translation": "FOMC声明",'
    ' "summary_translation": "委員会は誘導目標を0.25ポイント引き下げた。"}'
)
BRIEF_REPLY = (
    '{"title": {"en": "Fed cuts rates", "ja": "FRBが利下げ"},'
    ' "points": [{"en": "The target range was lowered [1].",'
    ' "ja": "誘導目標が引き下げられた。", "documents": [1]},'
    ' {"en": "No documents here.", "ja": "", "documents": [9]},'
    ' {"en": "Inflation remains elevated.", "ja": "物価は高止まり。", "documents": [1, 2]}]}'
)


def test_summaries_store_text_and_translation(api: Api) -> None:
    client, ids, _ = api
    llm = ScriptedLLM([SUMMARY_REPLY])
    with client.app.state.runtime.pool.connection() as conn:  # type: ignore[attr-defined]
        done, cost = summarize_pending(conn, llm, limit=10)
        assert done == 1  # only the statement carries a body long enough to summarise
        assert cost > 0
        row = conn.execute(
            "SELECT d.summary, d.summary_model, t.lang, t.title, t.summary AS ts "
            "FROM documents d JOIN document_translations t ON t.document_id = d.id WHERE d.id = %s",
            (ids["statement"],),
        ).fetchone()
        assert row is not None
        assert row["summary"].startswith("The Committee lowered")
        assert (row["lang"], row["title"], row["summary_model"]) == ("ja", "FOMC声明", "scripted")
        assert summarize_pending(conn, ScriptedLLM(), limit=10) == (0, 0.0)
    # the translation reaches the Japanese interface
    page = client.get(f"/doc/{ids['statement']}", params={"lang": "ja"}).text
    assert "FOMC声明" in page


def test_daily_and_meeting_briefs(api: Api) -> None:
    client, _, _ = api
    llm = ScriptedLLM([BRIEF_REPLY, BRIEF_REPLY])
    with client.app.state.runtime.pool.connection() as conn:  # type: ignore[attr-defined]
        assert daily_brief(conn, llm, date(2024, 1, 1)) is None  # nothing published that day
        brief_id = daily_brief(conn, llm, date(2025, 9, 17))
        assert brief_id is not None
        brief = conn.execute("SELECT * FROM briefs WHERE id = %s", (brief_id,)).fetchone()
        assert brief is not None
        assert brief["kind"] == "daily"
        assert brief["title"]["ja"] == "FRBが利下げ"
        assert len(brief["points"]) == 2  # the point citing document 9 was dropped
        assert set(brief["institutions"]) == {"fed"}

        pending = meetings_without_brief(conn, limit=5)
        assert [m.key for m in pending] == ["fed:20250917"]
        meeting_id = meeting_brief(conn, llm, pending[0])
        assert meeting_id is not None
        assert meetings_without_brief(conn, limit=5) == []
        assert list_meetings(conn)[0].key == "fed:20250917"
    page = client.get("/archive/briefs").text
    assert "Fed cuts rates" in page
    detail = client.get(f"/brief/{brief_id}").text
    assert "The target range was lowered" in detail
    meetings_page = client.get("/archive/meetings").text
    assert "Fed cuts rates" in meetings_page
