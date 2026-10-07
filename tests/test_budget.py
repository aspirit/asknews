from __future__ import annotations

import pytest

from asknews.answer.llm import Completion, ScriptedLLM
from asknews.budget import GENERATION_SHARE, generation_allowed, record_usage, spent_last_day
from asknews.db.connection import Connection
from asknews.db.migrate import migrate
from asknews.generate.summaries import summarize_pending
from tests.conftest import seed_archive

pytestmark = pytest.mark.integration


def test_spend_counts_answers_and_generation_together(db: Connection) -> None:
    migrate(db)
    assert spent_last_day(db) == 0.0
    db.execute(
        "INSERT INTO ask_requests (lang, question, status, cost_usd) "
        "VALUES ('en', 'q', 'answered', 0.30)"
    )
    record_usage(
        db,
        purpose="brief",
        completion=Completion(
            "{}", "claude-sonnet-5-5", input_tokens=100_000, output_tokens=10_000
        ),
        reference="fed:20250917",
    )
    # 100k input at $2/M plus 10k output at $10/M
    assert spent_last_day(db) == pytest.approx(0.30 + 0.20 + 0.10)
    assert generation_allowed(db, None)
    assert generation_allowed(db, 2.0)  # 0.60 spent, share is 1.00
    assert not generation_allowed(db, 1.0)  # share is 0.50
    assert GENERATION_SHARE == 0.5


def test_generation_stops_at_its_share_of_the_budget(db: Connection) -> None:
    seed_archive(db)
    db.execute(
        "INSERT INTO ask_requests (lang, question, status, cost_usd) "
        "VALUES ('en', 'q', 'answered', 0.60)"
    )
    run = summarize_pending(db, ScriptedLLM(['{"summary": "x"}']), limit=10, budget=1.0)
    assert (run.done, run.budget_reached) == (0, True)
    run = summarize_pending(db, ScriptedLLM(['{"summary": "x"}']), limit=10, budget=2.0)
    assert (run.done, run.budget_reached) == (1, False)
