from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from asknews.answer.llm import Completion, ScriptedLLM, estimate_cost
from asknews.answer.pipeline import MAX_ANSWER_TOKENS, RESERVATION_INPUT_TOKENS, Limits, ask
from asknews.db.connection import Connection, connect
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
        "VALUES ('en', 'q', 'answered', 0.50, 'c2')"
    )
    # Still room for an answer at the reserved price: the client's rate decides.
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


class ExplodingLLM:
    model = "exploding"

    def complete(self, *, system: str, user: str, max_tokens: int) -> Completion:
        del system, user, max_tokens
        raise ConnectionError("no network")


class SlowLLM(ScriptedLLM):
    """Answers after a pause, long enough for every thread to pass the checks."""

    def complete(self, *, system: str, user: str, max_tokens: int) -> Completion:
        time.sleep(0.3)
        return super().complete(system=system, user=user, max_tokens=max_tokens)


def test_concurrent_questions_cannot_exceed_the_client_rate(
    db: Connection, test_database_url: str
) -> None:
    seed(db)
    limits = Limits(daily_budget_usd=50.0, per_client_hour=2, per_client_day=10)
    llm = SlowLLM()
    outcomes: list[tuple[str, str | None]] = []
    gate = threading.Barrier(6)

    def worker() -> None:
        with connect(test_database_url, autocommit=True) as conn:
            gate.wait()
            result = ask(
                conn,
                HashEmbedder(),
                llm,
                "What did the Fed decide about the federal funds rate?",
                directory=DIRECTORY,
                vocab=VOCAB,
                client_hash="same",
                limits=limits,
            )
            outcomes.append((result.status, result.reason))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == [("answered", None)] * 2 + [("declined", "rate")] * 4
    assert len(llm.calls) == 2
    pending = db.execute("SELECT count(*) AS n FROM ask_requests WHERE status = 'pending'")
    assert pending.fetchone() == {"n": 0}


def test_concurrent_questions_cannot_exceed_the_budget(
    db: Connection, test_database_url: str
) -> None:
    seed(db)
    # Room for exactly one answer at the reserved price; the real cost is far lower.
    one = estimate_cost("scripted", RESERVATION_INPUT_TOKENS, MAX_ANSWER_TOKENS)
    limits = Limits(daily_budget_usd=one * 1.5, per_client_hour=100, per_client_day=100)
    llm = SlowLLM()
    outcomes: list[tuple[str, str | None]] = []
    gate = threading.Barrier(4)

    def worker(n: int) -> None:
        with connect(test_database_url, autocommit=True) as conn:
            gate.wait()
            result = ask(
                conn,
                HashEmbedder(),
                llm,
                "What did the Fed decide about the federal funds rate?",
                directory=DIRECTORY,
                vocab=VOCAB,
                client_hash=f"c{n}",
                limits=limits,
            )
            outcomes.append((result.status, result.reason))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == [("answered", None)] + [("declined", "budget")] * 3
    # Once the answer is in, its row carries the real cost, not the reservation.
    rows = db.execute("SELECT cost_usd FROM ask_requests WHERE status = 'answered'").fetchall()
    assert len(rows) == 1
    assert 0 < float(rows[0]["cost_usd"]) < one


class ExplodingEmbedder(HashEmbedder):
    def embed_query(self, text: str) -> list[float]:
        del text
        raise RuntimeError("model file missing")


def test_failure_before_the_model_releases_the_reservation(db: Connection) -> None:
    seed(db)
    with pytest.raises(RuntimeError):
        ask(
            db,
            ExplodingEmbedder(),
            ScriptedLLM(),
            "What did the Fed decide about the federal funds rate?",
            directory=DIRECTORY,
            vocab=VOCAB,
        )
    rows = db.execute("SELECT status, meta->>'error' AS error FROM ask_requests").fetchall()
    assert [(r["status"], r["error"]) for r in rows] == [("error", "RuntimeError")]


def test_model_failure_is_an_error_outcome(db: Connection) -> None:
    seed(db)
    result = ask(
        db,
        HashEmbedder(),
        ExplodingLLM(),
        "What did the Fed decide about the federal funds rate?",
        directory=DIRECTORY,
        vocab=VOCAB,
    )
    assert (result.status, result.reason) == ("error", "model")
    assert result.citations
    assert db.execute("SELECT status FROM ask_requests").fetchone()["status"] == "error"  # type: ignore[index]
