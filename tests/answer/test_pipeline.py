from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from asknews.answer.llm import ScriptedLLM, estimate_cost
from asknews.answer.pipeline import Limits, ask
from asknews.db.connection import Connection
from asknews.db.migrate import migrate
from asknews.domain_config import load_speakers
from asknews.embed.base import HashEmbedder
from asknews.embed.worker import embed_pending
from asknews.ingest.store import Document, Segment, replace_segments, upsert_document
from asknews.retrieval.gate import load_vocabulary

REPO_ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = load_speakers(REPO_ROOT / "domain" / "speakers.yaml")
VOCAB = load_vocabulary(REPO_ROOT / "domain" / "gate.yaml")
pytestmark = pytest.mark.integration


def seed(db: Connection) -> None:
    migrate(db)
    doc = upsert_document(
        db,
        Document(
            "fed",
            "statement",
            "en",
            "20250917",
            "Federal Reserve issues FOMC statement",
            datetime(2025, 9, 17, tzinfo=UTC),
            "https://fed.test/s",
        ),
    )
    replace_segments(
        db,
        doc,
        [
            Segment(
                0, "The Committee decided to lower the target range for the federal funds rate."
            ),
            Segment(1, "Inflation has moved up and remains somewhat elevated."),
        ],
    )
    embed_pending(db, HashEmbedder())


def run(db: Connection, question: str, llm: ScriptedLLM, **kwargs: object) -> object:
    return ask(db, HashEmbedder(), llm, question, directory=DIRECTORY, vocab=VOCAB, **kwargs)  # type: ignore[arg-type]


def test_answer_is_renumbered_and_logged(db: Connection) -> None:
    seed(db)
    llm = ScriptedLLM(
        [
            '{"paragraphs": ["Rates were lowered [2][9].", "Inflation stayed elevated [1]."],'
            ' "followups": ["What about the vote?"]}'
        ]
    )
    result = ask(
        db,
        HashEmbedder(),
        llm,
        "What did the Fed decide about the federal funds rate?",
        directory=DIRECTORY,
        vocab=VOCAB,
        client_hash="c1",
    )
    assert result.status == "answered"
    assert result.paragraphs == ["Rates were lowered [1].", "Inflation stayed elevated [2]."]
    assert [c.passage.text[:14] for c in result.citations] == ["Inflation has ", "The Committee "]
    assert result.followups == ["What about the vote?"]
    assert "Sources:" in llm.calls[0][1]
    row = db.execute(
        "SELECT status, model, cost_usd, client_hash, answer FROM ask_requests"
    ).fetchone()
    assert row is not None
    assert (row["status"], row["model"], row["client_hash"]) == ("answered", "scripted", "c1")
    assert row["answer"]["paragraphs"][0] == "Rates were lowered [1]."


def test_out_of_scope_is_declined_without_calling_the_model(db: Connection) -> None:
    seed(db)
    llm = ScriptedLLM()
    result = ask(db, HashEmbedder(), llm, "Best ramen in Tokyo?", directory=DIRECTORY, vocab=VOCAB)
    assert (result.status, result.reason) == ("declined", "out_of_scope")
    assert llm.calls == []
    assert db.execute("SELECT status FROM ask_requests").fetchone()["status"] == "declined"  # type: ignore[index]


def test_budget_and_rate_limits(db: Connection) -> None:
    seed(db)
    llm = ScriptedLLM()
    tight = Limits(daily_budget_usd=1.0, per_client_hour=1, per_client_day=10)
    db.execute(
        "INSERT INTO ask_requests (lang, question, status, cost_usd, client_hash) "
        "VALUES ('en', 'q', 'answered', 0.99, 'c2')"
    )
    first = ask(
        db,
        HashEmbedder(),
        llm,
        "What did the Fed decide?",
        directory=DIRECTORY,
        vocab=VOCAB,
        client_hash="c2",
        limits=tight,
    )
    assert (first.status, first.reason) == ("declined", "rate")
    db.execute("UPDATE ask_requests SET cost_usd = 1.5")
    second = ask(
        db,
        HashEmbedder(),
        llm,
        "What did the Fed decide?",
        directory=DIRECTORY,
        vocab=VOCAB,
        client_hash="c3",
        limits=tight,
    )
    assert (second.status, second.reason) == ("declined", "budget")


def test_ungrounded_reply_becomes_no_sources(db: Connection) -> None:
    seed(db)
    llm = ScriptedLLM(['{"paragraphs": ["The sources do not address this."], "followups": []}'])
    result = ask(
        db,
        HashEmbedder(),
        llm,
        "What did the Fed say about Bitcoin reserves?",
        directory=DIRECTORY,
        vocab=VOCAB,
    )
    assert result.status == "no_sources"
    assert result.paragraphs == ["The sources do not address this."]


def test_cost_estimate() -> None:
    assert estimate_cost("claude-opus-5-5", 1_000_000, 0) == 4.0
    assert estimate_cost("unknown-model", 0, 1_000_000) == 20.0
