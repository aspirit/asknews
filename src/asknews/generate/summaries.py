"""One- or two-sentence summaries of documents, plus a title and summary in
the other interface language, written by the model from the document text."""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from typing import Any

from asknews.answer.llm import LLM, Completion
from asknews.budget import generation_allowed, record_usage
from asknews.db.connection import Connection, connect
from asknews.domain_config import glossary
from asknews.generate.common import LANGUAGE_NAMES, clip, other_language, parse_json_object
from asknews.settings import get_settings

LOG = logging.getLogger(__name__)

BODY_CHARS = 6000
DEFAULT_KINDS = ("statement", "press_conference", "minutes", "report", "speech", "release")

SYSTEM = """You write catalogue entries for an archive of official central bank publications. \
Given one document, reply with JSON only:
{{"summary": "...", "title_translation": "...", "summary_translation": "..."}}
- summary: one or two sentences in {lang}, stating what the document decides, reports or \
argues. Use only the document's own content; keep numbers and dates exact; no evaluation.
- title_translation: the document's title rendered in {other}. Render document names as \
in this list: {glossary}.
- summary_translation: the summary rendered in {other}.
Return nothing but the JSON object."""


@dataclass(frozen=True, slots=True)
class Run:
    done: int
    cost: float  # estimated, in USD
    budget_reached: bool = False


@dataclass(frozen=True, slots=True)
class Generated:
    document_id: int
    summary: str
    translation_lang: str
    title_translation: str
    summary_translation: str
    completion: Completion


def pending_documents(
    conn: Connection, *, kinds: tuple[str, ...], limit: int
) -> list[dict[str, Any]]:
    return conn.execute(
        """
        SELECT d.id, d.kind, d.lang, d.title, d.body, d.published_at, i.name AS institution
          FROM documents d JOIN institutions i ON i.id = d.institution_id
         WHERE d.summary IS NULL AND d.body IS NOT NULL AND length(d.body) > 200
           AND d.kind = ANY(%s)
         ORDER BY CASE
                    WHEN i.code IN ('fed', 'boj') AND d.kind <> 'release'
                         AND d.published_at >= now() - interval '90 days' THEN 0
                    WHEN d.published_at >= now() - interval '7 days' THEN 1
                    WHEN i.code IN ('fed', 'boj') AND d.kind <> 'release' THEN 2
                    ELSE 3
                  END,
                  d.published_at DESC
         LIMIT %s
        """,
        (list(kinds), limit),
    ).fetchall()


def user_message(doc: dict[str, Any]) -> str:
    return (
        f"Publisher: {doc['institution']}\nKind: {doc['kind']}\n"
        f"Date: {doc['published_at']:%Y-%m-%d}\nTitle: {doc['title']}\n\n"
        f"Text:\n{clip(doc['body'], BODY_CHARS)}"
    )


def summarize_document(llm: LLM, doc: dict[str, Any]) -> tuple[Generated | None, Completion]:
    """The generated entry, or None when the reply was unusable, and the
    completion itself, which cost money either way."""
    other = other_language(doc["lang"])
    system = SYSTEM.format(
        lang=LANGUAGE_NAMES[doc["lang"]],
        other=LANGUAGE_NAMES[other],
        glossary="; ".join(f"{ja} = {en}" for ja, en in glossary()),
    )
    completion = llm.complete(system=system, user=user_message(doc), max_tokens=600)
    data = parse_json_object(completion.text)
    summary = str(data.get("summary") or "").strip()
    if not summary:
        LOG.warning("document %s: no summary in reply", doc["id"])
        return None, completion
    return Generated(
        int(doc["id"]),
        summary,
        other,
        str(data.get("title_translation") or "").strip() or str(doc["title"]),
        str(data.get("summary_translation") or "").strip(),
        completion,
    ), completion


def store(conn: Connection, generated: Generated) -> None:
    with conn.transaction():
        conn.execute(
            "UPDATE documents SET summary = %s, summary_model = %s WHERE id = %s",
            (generated.summary, generated.completion.model, generated.document_id),
        )
        conn.execute(
            """
            INSERT INTO document_translations (document_id, lang, title, summary, model)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (document_id, lang) DO UPDATE
               SET title = CASE WHEN document_translations.model = 'pattern'
                                THEN document_translations.title ELSE EXCLUDED.title END,
                   summary = EXCLUDED.summary,
                   model = CASE WHEN document_translations.model = 'pattern'
                                THEN document_translations.model ELSE EXCLUDED.model END,
                   generated_at = now()
            """,
            (
                generated.document_id,
                generated.translation_lang,
                generated.title_translation,
                generated.summary_translation or None,
                generated.completion.model,
            ),
        )


def summarize_pending(
    conn: Connection,
    llm: LLM,
    *,
    kinds: tuple[str, ...] = DEFAULT_KINDS,
    limit: int = 50,
    budget: float | None = None,
) -> Run:
    """Summarize up to ``limit`` documents while the daily budget's generation
    share lasts. Order: the central banks' recent publications (what the home
    page and meeting timeline show), then this week's releases from everyone,
    then the central banks' backlog, then the rest; newest first within each."""
    done = 0
    cost = 0.0
    for doc in pending_documents(conn, kinds=kinds, limit=limit):
        if not generation_allowed(conn, budget):
            return Run(done, cost, budget_reached=True)
        generated, completion = summarize_document(llm, doc)
        record_usage(conn, purpose="summary", completion=completion, reference=str(doc["id"]))
        cost += completion.cost_usd
        if generated is None:
            continue
        store(conn, generated)
        done += 1
    return Run(done, cost)


def llm_from_settings() -> LLM:
    settings = get_settings()
    if settings.llm == "claude":
        from asknews.answer.llm import ClaudeLLM

        return ClaudeLLM(settings.summary_model, effort="low")
    from asknews.answer.llm import ScriptedLLM

    return ScriptedLLM()


def run(args: argparse.Namespace) -> int:
    with connect(autocommit=True) as conn:
        result = summarize_pending(
            conn,
            llm_from_settings(),
            kinds=tuple(args.kinds),
            limit=args.limit,
            budget=get_settings().daily_budget_usd,
        )
    note = "; stopped at the daily budget's generation share" if result.budget_reached else ""
    sys.stdout.write(
        f"{result.done} documents summarized, estimated cost ${result.cost:.3f}{note}\n"
    )
    return 0


def add_cli(subparsers: Any) -> None:
    parser = subparsers.add_parser("summarize", help="generate summaries and title translations")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--kinds", nargs="+", default=list(DEFAULT_KINDS))
    parser.set_defaults(func=run)
