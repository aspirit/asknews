"""The daily spending cap, shared by answers and generation.

Every model call is recorded with an estimated cost: answers in
``ask_requests``, summaries and briefs in ``llm_usage``. The cap is a
rolling 24 hours over both. Generation may use only a share of it, so that
questions can still be answered for the rest of the day after the daily
job has run; answers stop at the cap itself.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from asknews.answer.llm import Completion
from asknews.db.connection import Connection

GENERATION_SHARE = 0.5
WINDOW = timedelta(days=1)
# A request reserved but never completed (the process died) stops counting
# against the cap after this long.
PENDING_TTL = timedelta(minutes=10)


def spent_last_day(conn: Connection) -> float:
    """Estimated spend over the last 24 hours, answers and generation together."""
    now = datetime.now(UTC)
    since = now - WINDOW
    row = conn.execute(
        """
        SELECT (SELECT coalesce(sum(cost_usd), 0) FROM ask_requests
                 WHERE asked_at >= %s AND (status <> 'pending' OR asked_at >= %s))
             + (SELECT coalesce(sum(cost_usd), 0) FROM llm_usage WHERE called_at >= %s) AS spent
        """,
        (since, now - PENDING_TTL, since),
    ).fetchone()
    return float(row["spent"]) if row else 0.0


def generation_allowed(conn: Connection, budget: float | None) -> bool:
    """Whether one more generation call fits under the generation share of the cap."""
    return budget is None or spent_last_day(conn) < budget * GENERATION_SHARE


def record_usage(
    conn: Connection, *, purpose: str, completion: Completion, reference: str | None = None
) -> None:
    conn.execute(
        """
        INSERT INTO llm_usage (purpose, model, input_tokens, output_tokens, cost_usd, reference)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            purpose,
            completion.model,
            completion.input_tokens,
            completion.output_tokens,
            completion.cost_usd,
            reference,
        ),
    )
