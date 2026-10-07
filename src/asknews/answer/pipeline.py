"""From a question to a cited answer, with the guards around it.

Order of checks: the daily budget, the per-client rate, whether the question
is about the archive, whether anything was retrieved; only then the model.
Every outcome is recorded in ``ask_requests``.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from asknews.answer.faithfulness import CheckedAnswer, check
from asknews.answer.llm import LLM
from asknews.answer.prompt import system_prompt, user_message
from asknews.db.connection import Connection
from asknews.domain_config import SpeakerDirectory
from asknews.embed.base import Embedder
from asknews.retrieval import retrieve
from asknews.retrieval.fusion import Citation
from asknews.retrieval.gate import Vocabulary
from asknews.retrieval.query import detect_language

LOG = logging.getLogger(__name__)
MAX_ANSWER_TOKENS = 2000
_CITATION = re.compile(r"\[(\d{1,2})\]")


@dataclass(frozen=True, slots=True)
class Limits:
    daily_budget_usd: float = 3.0
    per_client_hour: int = 30
    per_client_day: int = 120


DEFAULT_LIMITS = Limits()


@dataclass(frozen=True, slots=True)
class AskResult:
    status: str  # answered | declined | no_sources | error
    lang: str
    paragraphs: list[str] = field(default_factory=list)
    followups: list[str] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)  # in citation-number order
    reason: str | None = None  # for declined: budget | rate | out_of_scope | refused
    meta: dict[str, Any] = field(default_factory=dict)
    request_id: int | None = None


def spent_today(conn: Connection) -> float:
    row = conn.execute(
        "SELECT coalesce(sum(cost_usd), 0) AS spent FROM ask_requests WHERE asked_at >= %s",
        (datetime.now(UTC) - timedelta(days=1),),
    ).fetchone()
    return float(row["spent"]) if row else 0.0


def client_requests(conn: Connection, client_hash: str, *, since: timedelta) -> int:
    row = conn.execute(
        "SELECT count(*) AS n FROM ask_requests WHERE client_hash = %s AND asked_at >= %s",
        (client_hash, datetime.now(UTC) - since),
    ).fetchone()
    return int(row["n"]) if row else 0


def renumber(checked: CheckedAnswer, citations: list[Citation]) -> tuple[list[str], list[Citation]]:
    """Give the cited sources consecutive numbers in order of first use."""
    mapping = {old: new for new, old in enumerate(checked.used, start=1)}
    paragraphs = [
        _CITATION.sub(lambda m: f"[{mapping[int(m.group(1))]}]", p) for p in checked.paragraphs
    ]
    return paragraphs, [citations[old - 1] for old in checked.used]


def _log(
    conn: Connection,
    *,
    lang: str,
    question: str,
    status: str,
    client_hash: str | None,
    started: float,
    citations: list[Citation] = (),  # type: ignore[assignment]
    answer: dict[str, Any] | None = None,
    model: str | None = None,
    tokens: tuple[int, int] | None = None,
    cost: float | None = None,
    meta: dict[str, Any] | None = None,
) -> int:
    row = conn.execute(
        """
        INSERT INTO ask_requests
            (lang, question, status, answer, citations, model, input_tokens, output_tokens,
             cost_usd, latency_ms, client_hash, meta)
        VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s, %s::jsonb)
        RETURNING id
        """,
        (
            lang,
            question[:2000],
            status,
            json.dumps(answer, ensure_ascii=False) if answer is not None else None,
            json.dumps(
                [
                    {
                        "segment": c.passage.segment_id,
                        "document": c.passage.document_id,
                        "score": round(c.score, 4),
                        "paths": list(c.paths),
                    }
                    for c in citations
                ]
            ),
            model,
            tokens[0] if tokens else None,
            tokens[1] if tokens else None,
            cost,
            int((time.monotonic() - started) * 1000),
            client_hash,
            json.dumps(meta or {}, ensure_ascii=False, default=str),
        ),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def ask(
    conn: Connection,
    embedder: Embedder,
    llm: LLM,
    question: str,
    *,
    directory: SpeakerDirectory,
    vocab: Vocabulary,
    client_hash: str | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> AskResult:
    started = time.monotonic()
    question = question.strip()
    lang = detect_language(question)

    def declined(reason: str, meta: dict[str, Any] | None = None) -> AskResult:
        request_id = _log(
            conn,
            lang=lang,
            question=question,
            status="declined",
            client_hash=client_hash,
            started=started,
            meta={"reason": reason, **(meta or {})},
        )
        return AskResult("declined", lang, reason=reason, meta=meta or {}, request_id=request_id)

    if spent_today(conn) >= limits.daily_budget_usd:
        return declined("budget")
    if client_hash and (
        client_requests(conn, client_hash, since=timedelta(hours=1)) >= limits.per_client_hour
        or client_requests(conn, client_hash, since=timedelta(days=1)) >= limits.per_client_day
    ):
        return declined("rate")

    retrieval = retrieve(conn, embedder, question, directory=directory, vocab=vocab)
    if not retrieval.in_domain:
        return declined("out_of_scope", retrieval.meta)
    if not retrieval.citations:
        request_id = _log(
            conn,
            lang=lang,
            question=question,
            status="no_sources",
            client_hash=client_hash,
            started=started,
            meta=retrieval.meta,
        )
        return AskResult("no_sources", lang, meta=retrieval.meta, request_id=request_id)

    try:
        completion = llm.complete(
            system=system_prompt(lang),
            user=user_message(question, retrieval.citations),
            max_tokens=MAX_ANSWER_TOKENS,
        )
    except Exception as exc:
        LOG.warning("model call failed: %s", exc)
        request_id = _log(
            conn,
            lang=lang,
            question=question,
            status="error",
            client_hash=client_hash,
            started=started,
            citations=retrieval.citations,
            meta={"error": type(exc).__name__},
        )
        return AskResult(
            "error", lang, citations=retrieval.citations, reason="model", request_id=request_id
        )
    tokens = (completion.input_tokens, completion.output_tokens)
    if completion.refused:
        request_id = _log(
            conn,
            lang=lang,
            question=question,
            status="declined",
            client_hash=client_hash,
            started=started,
            citations=retrieval.citations,
            model=completion.model,
            tokens=tokens,
            cost=completion.cost_usd,
            meta={"reason": "refused"},
        )
        return AskResult("declined", lang, reason="refused", request_id=request_id)

    checked = check(completion.text, len(retrieval.citations))
    paragraphs, cited = renumber(checked, retrieval.citations)
    status = "answered" if checked.is_grounded else "no_sources"
    meta = {**retrieval.meta, "warnings": checked.warnings, "used": checked.used}
    request_id = _log(
        conn,
        lang=lang,
        question=question,
        status=status,
        client_hash=client_hash,
        started=started,
        citations=cited,
        model=completion.model,
        tokens=tokens,
        cost=completion.cost_usd,
        meta=meta,
        answer={"paragraphs": paragraphs, "followups": checked.followups},
    )
    return AskResult(
        status, lang, paragraphs, checked.followups, cited, meta=meta, request_id=request_id
    )
