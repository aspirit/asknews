"""Repeatable retrieval checks, including evidence passages and domain refusals."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from asknews.db.connection import Connection, connect
from asknews.domain_config import SpeakerDirectory, speakers
from asknews.embed.base import Embedder
from asknews.embed.select import embedder_from_settings
from asknews.retrieval import retrieve
from asknews.retrieval.fusion import MAX_CITATIONS
from asknews.retrieval.gate import Vocabulary, vocabulary
from asknews.retrieval.pipeline import RetrievalMode
from asknews.settings import get_settings

Status = Literal["retrieved", "out_of_scope", "no_sources"]
_MATCH_FIELDS = frozenset(
    {"institution", "kind", "external_id", "title_contains", "passage_contains"}
)


@dataclass(frozen=True, slots=True)
class Case:
    question: str
    expect: dict[str, str]
    within: int = 5
    outcome: Status = "retrieved"
    id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.question, str) or not self.question.strip():
            raise ValueError("each case needs a non-empty question")
        if type(self.within) is not int or not 1 <= self.within <= MAX_CITATIONS:
            raise ValueError(f"within must be between 1 and {MAX_CITATIONS}")
        if self.outcome not in ("retrieved", "out_of_scope", "no_sources"):
            raise ValueError(f"unknown expected outcome: {self.outcome}")
        if not isinstance(self.expect, dict) or set(self.expect) - _MATCH_FIELDS:
            raise ValueError("expect contains unsupported document or passage fields")
        if any(not isinstance(v, str) or not v.strip() for v in self.expect.values()):
            raise ValueError("expect values must be non-empty strings; quote numeric identifiers")
        if (self.outcome == "retrieved") != bool(self.expect):
            raise ValueError("only retrieved cases must have a non-empty expect mapping")
        if not isinstance(self.id, str):
            raise TypeError("case id must be a string")


@dataclass(frozen=True, slots=True)
class Outcome:
    case: Case
    rank: int | None  # 1-based rank of the first matching citation
    citations: int
    status: Status = "retrieved"
    elapsed_ms: float = 0.0

    @property
    def passed(self) -> bool:
        if self.status != self.case.outcome:
            return False
        if self.case.outcome != "retrieved":
            return True
        return self.rank is not None and self.rank <= self.case.within


def load_cases(path: Path) -> list[Case]:
    raw = yaml.safe_load(path.read_text("utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("cases"), list) or not raw["cases"]:
        raise ValueError("cases must be a non-empty list")
    cases: list[Case] = []
    ids: set[str] = set()
    for index, item in enumerate(raw["cases"], start=1):
        if not isinstance(item, dict) or set(item) - {
            "question",
            "expect",
            "within",
            "outcome",
            "id",
        }:
            raise ValueError(f"case {index}: unknown fields or not a mapping")
        case = Case(
            question=item.get("question", ""),
            expect=item.get("expect", {}),
            within=item.get("within", 5),
            outcome=item.get("outcome", "retrieved"),
            id=item.get("id", f"case-{index}"),
        )
        if not case.id.strip() or case.id in ids:
            raise ValueError(f"empty or duplicate case id: {case.id!r}")
        ids.add(case.id)
        cases.append(case)
    return cases


def matches(expect: dict[str, str], row: dict[str, Any]) -> bool:
    for key, value in expect.items():
        if key in ("title_contains", "passage_contains"):
            field = "title" if key == "title_contains" else "text"
            needle = " ".join(value.casefold().split())
            if needle not in " ".join(str(row.get(field, "")).casefold().split()):
                return False
        elif str(row.get(key)) != value:
            return False
    return True


def evaluate(
    conn: Connection,
    embedder: Embedder,
    cases: list[Case],
    *,
    directory: SpeakerDirectory,
    vocab: Vocabulary,
    mode: RetrievalMode = "hybrid",
    today: date | None = None,
) -> list[Outcome]:
    outcomes: list[Outcome] = []
    for case in cases:
        started = time.perf_counter()
        result = retrieve(
            conn, embedder, case.question, directory=directory, vocab=vocab, mode=mode, today=today
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        status: Status = (
            "out_of_scope"
            if not result.in_domain
            else "retrieved"
            if result.citations
            else "no_sources"
        )
        docs = conn.execute(
            "SELECT d.id, d.kind, d.title, d.external_id, i.code AS institution FROM documents d "
            "JOIN institutions i ON i.id = d.institution_id WHERE d.id = ANY(%s)",
            (list({c.passage.document_id for c in result.citations}),),
        ).fetchall()
        by_id = {doc["id"]: dict(doc) for doc in docs}
        rank: int | None = None
        if case.outcome == "retrieved":
            for n, citation in enumerate(result.citations, start=1):
                doc = by_id.get(citation.passage.document_id)
                if doc is not None and matches(case.expect, {**doc, "text": citation.passage.text}):
                    rank = n
                    break
        outcomes.append(Outcome(case, rank, len(result.citations), status, elapsed_ms))
    return outcomes


def metrics(outcomes: list[Outcome]) -> dict[str, int | float | None]:
    positives = [o for o in outcomes if o.case.outcome == "retrieved"]
    negatives = [o for o in outcomes if o.case.outcome != "retrieved"]
    times = sorted(o.elapsed_ms for o in outcomes)
    return {
        "cases": len(outcomes),
        "passed": sum(o.passed for o in outcomes),
        "positive_cases": len(positives),
        "hit_at_5": (
            sum(o.rank is not None and o.rank <= 5 for o in positives) / len(positives)
            if positives
            else None
        ),
        "mrr_at_12": sum(1 / o.rank if o.rank else 0 for o in positives) / len(positives)
        if positives
        else None,
        "negative_cases": len(negatives),
        "negative_accuracy": sum(o.passed for o in negatives) / len(negatives)
        if negatives
        else None,
        "p50_ms": times[math.ceil(len(times) * 0.50) - 1] if times else None,
        "p95_ms": times[math.ceil(len(times) * 0.95) - 1] if times else None,
    }


def corpus_inventory(conn: Connection) -> dict[str, Any]:
    """A compact inventory, not a claim that two corpora contain identical text."""
    row = conn.execute(
        "SELECT (SELECT count(*) FROM documents) AS documents, "
        "(SELECT count(*) FROM segments) AS segments"
    ).fetchone()
    assert row is not None
    models = conn.execute(
        "SELECT model, count(*) AS segments FROM segment_embeddings GROUP BY model ORDER BY model"
    ).fetchall()
    return {**dict(row), "embeddings": models}


class NoEmbedder:
    """Stands in for an embedder where none may be used; any call is a bug."""

    model_name = "none"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError(f"lexical evaluation must not embed ({len(texts)} texts)")

    def embed_query(self, text: str) -> list[float]:
        raise RuntimeError(f"lexical evaluation must not embed: {text[:40]!r}")


def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    path = Path(args.cases) if args.cases else settings.domain_dir / "eval_cases.yaml"
    try:
        cases = load_cases(path)
    except (TypeError, ValueError, yaml.YAMLError) as exc:
        sys.stderr.write(f"invalid evaluation cases: {exc}\n")
        return 2
    today = args.as_of or datetime.now(UTC).date()
    # Lexical runs measure the keyword path alone: they must not depend on the
    # embedding service, not even to name its model in the report.
    embedder: Embedder = NoEmbedder() if args.mode == "lexical" else embedder_from_settings()
    with connect(autocommit=True) as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        corpus = corpus_inventory(conn)
        outcomes = evaluate(
            conn,
            embedder,
            cases,
            directory=speakers(),
            vocab=vocabulary(),
            mode=args.mode,
            today=today,
        )
    for o in outcomes:
        status = "pass" if o.passed else "FAIL"
        rank = f"rank {o.rank}" if o.rank else o.status
        sys.stdout.write(f"{status}  {rank:12s}  {o.elapsed_ms:8.1f} ms  {o.case.question}\n")
    summary = metrics(outcomes)
    sys.stdout.write(f"{summary['passed']}/{summary['cases']} cases passed\n")
    if args.report:
        report = {
            "schema_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "as_of": today.isoformat(),
            "mode": args.mode,
            "embedder": embedder.model_name,
            "cases_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "corpus_label": args.corpus_label,
            "corpus": corpus,
            "metrics": summary,
            "outcomes": [{**asdict(o), "passed": o.passed} for o in outcomes],
        }
        destination = Path(args.report)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return 0 if summary["passed"] == summary["cases"] else 1


def add_cli(subparsers: Any) -> None:
    parser = subparsers.add_parser("eval", help="check retrieval against the regression cases")
    parser.add_argument("--cases", help="path to a cases file (default: domain/eval_cases.yaml)")
    parser.add_argument("--mode", choices=("hybrid", "lexical", "dense"), default="hybrid")
    parser.add_argument(
        "--as-of", type=date.fromisoformat, help="reference date for relative questions"
    )
    parser.add_argument("--report", help="write a JSON report (also written when cases fail)")
    parser.add_argument(
        "--corpus-label", help="identifier of the corpus snapshot used for this run"
    )
    parser.set_defaults(func=run)
