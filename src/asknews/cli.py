"""Command-line entry point.

Usage::

    asknews migrate
    asknews ingest fed-presconf --limit 5

Source modules register their own sub-commands so this file stays a table
of contents.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence


def _migrate(args: argparse.Namespace) -> int:
    from asknews.db.migrate import main

    return main(["--dsn", args.dsn] if args.dsn else [])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="asknews", description="Archive maintenance commands.")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    commands = parser.add_subparsers(dest="command", required=True)

    migrate = commands.add_parser("migrate", help="apply pending database migrations")
    migrate.add_argument("--dsn", help="connection URL (default: DATABASE_URL)")
    migrate.set_defaults(func=_migrate)

    from asknews import evaluation, jobs, topics
    from asknews.embed import worker
    from asknews.generate import briefs, summaries

    topics.add_cli(commands)
    worker.add_cli(commands)
    summaries.add_cli(commands)
    briefs.add_cli(commands)
    jobs.add_cli(commands)
    evaluation.add_cli(commands)

    ingest = commands.add_parser("ingest", help="fetch publications into the archive")
    sources = ingest.add_subparsers(dest="source", required=True)
    from asknews.ingest import feeds
    from asknews.ingest.boj import kaiken, youtube
    from asknews.ingest.boj import releases as boj_releases
    from asknews.ingest.fed import presconf, releases

    presconf.add_cli(sources)
    releases.add_cli(sources)
    kaiken.add_cli(sources)
    boj_releases.add_cli(sources)
    feeds.add_cli(sources)
    youtube.add_cli(sources)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Request logging would print signed media URLs; the fetcher reports what matters.
    for name in ("httpx", "httpx2", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
