"""The scheduled job: fetch what is new, tag and embed it, write briefs and summaries.

Run once a day (``asknews daily``). Each step is the same CLI command a
person would run by hand, with its own log line; a failing step does not
stop the others.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

LOG = logging.getLogger(__name__)


def steps(year: int, *, summaries: int) -> list[tuple[str, list[str]]]:
    """``(label, argv)`` pairs in execution order."""
    since = ["--since", str(year)]
    return [
        ("fed press conferences", ["ingest", "fed-presconf", *since]),
        ("fed releases", ["ingest", "fed-releases", *since]),
        ("boj press conferences", ["ingest", "boj-kaiken", *since]),
        ("boj releases", ["ingest", "boj-releases", *since]),
        ("boj videos", ["ingest", "boj-videos"]),
        ("feeds", ["ingest", "feeds", "--days", "10"]),
        ("topics", ["topics"]),
        ("embed", ["embed"]),
        ("daily briefs", ["briefs", "daily", "--days", "3"]),
        ("meeting briefs", ["briefs", "meetings", "--limit", "3"]),
        ("summaries", ["summarize", "--limit", str(summaries)]),
    ]


def run_steps(
    plan: Sequence[tuple[str, list[str]]], *, runner: Callable[[list[str]], int]
) -> list[tuple[str, int]]:
    results: list[tuple[str, int]] = []
    for label, argv in plan:
        LOG.info("step: %s", label)
        try:
            code = runner(argv)
        except Exception:
            LOG.exception("step failed: %s", label)
            code = 1
        results.append((label, code))
    return results


def run(args: argparse.Namespace) -> int:
    from asknews.cli import main

    year = datetime.now(UTC).year
    plan = steps(year, summaries=args.summaries)
    if args.only:
        plan = [(label, argv) for label, argv in plan if argv[0] in args.only or label in args.only]
    if args.dry_run:
        for label, argv in plan:
            sys.stdout.write(f"{label:22s} asknews {' '.join(argv)}\n")
        return 0
    results = run_steps(plan, runner=main)
    for label, code in results:
        sys.stdout.write(f"{label:22s} {'ok' if code == 0 else 'FAILED'}\n")
    return 1 if any(code for _, code in results) else 0


def add_cli(subparsers: Any) -> None:
    parser = subparsers.add_parser("daily", help="fetch new material, embed it, write briefs")
    parser.add_argument("--summaries", type=int, default=40, help="documents to summarise per run")
    parser.add_argument("--only", nargs="*", help="run only steps whose command or label matches")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    parser.set_defaults(func=run)
