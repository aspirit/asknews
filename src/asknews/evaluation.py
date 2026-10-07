"""Retrieval regression check: does the expected document surface for each question?"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from asknews.db.connection import Connection, connect
from asknews.domain_config import SpeakerDirectory, speakers
from asknews.embed.base import Embedder
from asknews.embed.select import embedder_from_settings
from asknews.retrieval import retrieve
from asknews.retrieval.gate import Vocabulary, vocabulary
from asknews.settings import get_settings


@dataclass(frozen=True, slots=True)
class Case:
    question: str
    expect: dict[str, str]
    within: int = 5


@dataclass(frozen=True, slots=True)
class Outcome:
    case: Case
    rank: int | None  # 1-based rank of the first matching citation
    citations: int

    @property
    def passed(self) -> bool:
        return self.rank is not None and self.rank <= self.case.within


def load_cases(path: Path) -> list[Case]:
    raw = yaml.safe_load(path.read_text("utf-8")) or {}
    return [
        Case(
            str(c["question"]),
            {str(k): str(v) for k, v in (c.get("expect") or {}).items()},
            int(c.get("within", 5)),
        )
        for c in raw.get("cases") or []
    ]


def matches(expect: dict[str, str], row: dict[str, Any]) -> bool:
    for key, value in expect.items():
        if key == "title_contains":
            if value.lower() not in str(row["title"]).lower():
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
) -> list[Outcome]:
    outcomes: list[Outcome] = []
    for case in cases:
        result = retrieve(conn, embedder, case.question, directory=directory, vocab=vocab)
        rank: int | None = None
        for n, citation in enumerate(result.citations, start=1):
            doc = conn.execute(
                "SELECT d.kind, d.title, d.external_id, i.code AS institution FROM documents d "
                "JOIN institutions i ON i.id = d.institution_id WHERE d.id = %s",
                (citation.passage.document_id,),
            ).fetchone()
            if doc is not None and matches(case.expect, dict(doc)):
                rank = n
                break
        outcomes.append(Outcome(case, rank, len(result.citations)))
    return outcomes


def run(args: argparse.Namespace) -> int:
    cases = load_cases(
        Path(args.cases) if args.cases else get_settings().domain_dir / "eval_cases.yaml"
    )
    with connect(autocommit=True) as conn:
        outcomes = evaluate(
            conn, embedder_from_settings(), cases, directory=speakers(), vocab=vocabulary()
        )
    for o in outcomes:
        status = "pass" if o.passed else "FAIL"
        rank = f"rank {o.rank}" if o.rank else "absent"
        sys.stdout.write(f"{status}  {rank:9s} of {o.citations:2d}  {o.case.question}\n")
    passed = sum(o.passed for o in outcomes)
    sys.stdout.write(f"{passed}/{len(outcomes)} cases within rank\n")
    return 0 if passed == len(outcomes) else 1


def add_cli(subparsers: Any) -> None:
    parser = subparsers.add_parser("eval", help="check retrieval against the regression cases")
    parser.add_argument("--cases", help="path to a cases file (default: domain/eval_cases.yaml)")
    parser.set_defaults(func=run)
