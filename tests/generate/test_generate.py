from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from asknews.answer.llm import ScriptedLLM
from asknews.archive.meetings import list_meetings
from asknews.generate.briefs import daily_brief, meeting_brief, meetings_without_brief
from asknews.generate.summaries import Run, summarize_pending

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[2]
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


def test_summary_prompt_quotes_the_glossary() -> None:
    from asknews.domain_config import load_glossary
    from asknews.generate.summaries import SYSTEM

    pairs = load_glossary(REPO_ROOT / "domain" / "glossary.yaml")
    assert ("議事要旨", "Minutes of the Monetary Policy Meeting") in pairs
    rendered = SYSTEM.format(
        lang="Japanese", other="English", glossary="; ".join(f"{j} = {e}" for j, e in pairs)
    )
    assert "議事要旨 = Minutes of the Monetary Policy Meeting" in rendered


def test_summaries_store_text_and_translation(api: Api) -> None:
    client, ids, _ = api
    llm = ScriptedLLM([SUMMARY_REPLY])
    with client.app.state.runtime.pool.connection() as conn:  # type: ignore[attr-defined]
        run = summarize_pending(conn, llm, limit=10)
        assert run.done == 1  # only the statement carries a body long enough to summarise
        assert run.cost > 0
        assert not run.budget_reached
        usage = conn.execute("SELECT purpose, reference, cost_usd FROM llm_usage").fetchall()
        assert [(u["purpose"], u["reference"]) for u in usage] == [
            ("summary", str(ids["statement"]))
        ]
        assert float(usage[0]["cost_usd"]) == pytest.approx(run.cost)
        row = conn.execute(
            "SELECT d.summary, d.summary_model, t.lang, t.title, t.summary AS ts "
            "FROM documents d JOIN document_translations t ON t.document_id = d.id WHERE d.id = %s",
            (ids["statement"],),
        ).fetchone()
        assert row is not None
        assert row["summary"].startswith("The Committee lowered")
        assert (row["lang"], row["summary_model"]) == ("ja", "scripted")
        assert row["title"].startswith("FOMC声明")  # the pattern title outranks the model's
        assert summarize_pending(conn, ScriptedLLM(), limit=10) == Run(0, 0.0)
    # the translation reaches the Japanese interface
    page = client.get(f"/ja/doc/{ids['statement']}").text
    assert "FOMC声明" in page


def test_daily_and_meeting_briefs(api: Api) -> None:
    client, _, _ = api
    llm = ScriptedLLM([BRIEF_REPLY, BRIEF_REPLY, BRIEF_REPLY])
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
        # Regenerating a day replaces its brief (a NULL meeting key still collides).
        assert daily_brief(conn, llm, date(2025, 9, 17)) == brief_id
        assert conn.execute("SELECT count(*) AS n FROM briefs").fetchone() == {"n": 1}

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
