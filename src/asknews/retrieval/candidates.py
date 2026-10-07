"""Candidate passages from the two retrieval paths and their hydration.

A hard time window (a named month or year) is applied inside the SQL of
every path, so the limited candidate lists are spent on passages from the
period asked about rather than filtered away afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from asknews.db.connection import Connection
from asknews.embed.base import Embedder, to_pgvector
from asknews.retrieval.query import Query

LEXICAL_LIMIT = 60
SEMANTIC_LIMIT = 40
HNSW_EF_SEARCH = 100
FULLTEXT_AND_MIN = 10  # fewer all-terms hits than this: also search any-term
MAX_TERMS = 10


@dataclass(slots=True)
class Candidate:
    segment_id: int
    scores: dict[str, float] = field(default_factory=dict)  # path name -> raw score
    ranks: dict[str, int] = field(default_factory=dict)  # path name -> 1-based rank


def _window_sql(query: Query) -> tuple[str, list[Any]]:
    if query.window is None or not query.window.hard:
        return "", []
    end = query.window.end + timedelta(days=1)
    return " AND d.published_at >= %s AND d.published_at < %s", [query.window.start, end]


class _Collector:
    def __init__(self) -> None:
        self.found: dict[int, Candidate] = {}
        self.strict_fulltext = 0  # passages matching every content term

    def add(self, path: str, rows: list[dict[str, Any]], *, offset: int = 0) -> None:
        for rank, row in enumerate(rows, start=offset + 1):
            cand = self.found.setdefault(int(row["id"]), Candidate(int(row["id"])))
            cand.scores[path] = max(cand.scores.get(path, 0.0), float(row["score"]))
            cand.ranks.setdefault(path, rank)

    def count(self, path: str) -> int:
        return sum(1 for c in self.found.values() if path in c.ranks)


def _phrases(conn: Connection, query: Query, out: _Collector) -> None:
    window, params = _window_sql(query)
    conditions = " AND ".join("s.text ILIKE %s" for _ in query.phrases)
    rows = conn.execute(
        f"""
        SELECT s.id, {len(query.phrases)}::float AS score
          FROM segments s JOIN documents d ON d.id = s.document_id
         WHERE {conditions}{window}
         ORDER BY d.published_at DESC, s.id
         LIMIT %s
        """,
        [f"%{p}%" for p in query.phrases] + params + [LEXICAL_LIMIT],
    ).fetchall()
    out.add("phrase", rows)


def _fulltext(conn: Connection, query: Query, out: _Collector) -> None:
    """English full text: all content terms first, then any term if that was thin."""
    terms = list(query.terms[:MAX_TERMS]) or [query.text]
    window, params = _window_sql(query)
    for joiner in (" ", " or "):
        rows = conn.execute(
            f"""
            SELECT s.id, ts_rank_cd(s.tsv, q) AS score
              FROM segments s JOIN documents d ON d.id = s.document_id,
                   websearch_to_tsquery('english', %s) q
             WHERE s.lang = 'en' AND s.tsv @@ q{window}
             ORDER BY score DESC
             LIMIT %s
            """,
            [joiner.join(terms), *params, LEXICAL_LIMIT],
        ).fetchall()
        out.add("fulltext", rows, offset=0 if joiner == " " else out.count("fulltext"))
        if joiner == " ":
            out.strict_fulltext = len(rows)
        if out.count("fulltext") >= FULLTEXT_AND_MIN or len(terms) < 2:
            break


def _terms(conn: Connection, query: Query, out: _Collector) -> None:
    """Japanese: count how many content terms each passage contains."""
    terms = list(query.terms[:MAX_TERMS])
    window, params = _window_sql(query)
    hits = " + ".join("(s.text LIKE %s)::int" for _ in terms)
    any_term = " OR ".join("s.text LIKE %s" for _ in terms)
    rows = conn.execute(
        f"""
        SELECT id, score FROM (
            SELECT s.id, ({hits}) AS score, d.published_at
              FROM segments s JOIN documents d ON d.id = s.document_id
             WHERE s.lang <> 'en' AND ({any_term}){window}
        ) matched
         ORDER BY score DESC, published_at DESC, id DESC
         LIMIT %s
        """,
        [f"%{t}%" for t in terms] * 2 + params + [LEXICAL_LIMIT],
    ).fetchall()
    out.add("terms", rows)


@dataclass(slots=True)
class Lexical:
    candidates: list[Candidate]
    strict_fulltext: int  # English passages matching every content term


def lexical(conn: Connection, query: Query) -> Lexical:
    """Exact phrases in any language, then full text (English) or terms (Japanese)."""
    out = _Collector()
    if query.phrases:
        _phrases(conn, query, out)
    if query.lang == "en":
        _fulltext(conn, query, out)
    elif query.terms:
        _terms(conn, query, out)
    return Lexical(list(out.found.values()), out.strict_fulltext)


def semantic(conn: Connection, embedder: Embedder, query: Query) -> list[Candidate]:
    vector = to_pgvector(embedder.embed_query(query.text))
    window, params = _window_sql(query)
    join = (
        "JOIN segments s ON s.id = e.segment_id JOIN documents d ON d.id = s.document_id"
        if window
        else ""
    )
    with conn.transaction():
        conn.execute(f"SET LOCAL hnsw.ef_search = {HNSW_EF_SEARCH}")
        rows = conn.execute(
            f"""
            SELECT e.segment_id AS id, 1 - (e.embedding <=> %s::vector) AS score
              FROM segment_embeddings e {join}
             WHERE e.model = %s{window}
             ORDER BY e.embedding <=> %s::vector
             LIMIT %s
            """,
            [vector, embedder.model_name, *params, vector, SEMANTIC_LIMIT],
        ).fetchall()
    return [
        Candidate(int(row["id"]), {"semantic": float(row["score"])}, {"semantic": rank})
        for rank, row in enumerate(rows, start=1)
    ]


@dataclass(frozen=True, slots=True)
class Passage:
    segment_id: int
    document_id: int
    position: int
    institution: str
    institution_name: str
    kind: str
    lang: str
    title: str
    published_at: datetime
    source_url: str
    text: str
    speaker: str | None
    speaker_slug: str | None
    document_speaker: str | None
    start_sec: float | None
    end_sec: float | None
    media_provider: str | None
    media_id: str | None


def following_segment_ids(conn: Connection, segment_ids: list[int]) -> dict[int, int]:
    """For each segment, the id of the next segment of its document, when there is one."""
    if not segment_ids:
        return {}
    rows = conn.execute(
        """
        SELECT s.id, n.id AS next_id
          FROM segments s
          JOIN segments n ON n.document_id = s.document_id AND n.position = s.position + 1
         WHERE s.id = ANY(%s)
        """,
        (segment_ids,),
    ).fetchall()
    return {int(r["id"]): int(r["next_id"]) for r in rows}


def hydrate(conn: Connection, segment_ids: list[int]) -> dict[int, Passage]:
    if not segment_ids:
        return {}
    rows = conn.execute(
        """
        SELECT s.id, s.document_id, s.position, s.text, s.speaker, s.start_sec, s.end_sec,
               d.kind, d.lang, d.title, d.published_at, d.source_url,
               i.code AS institution, i.name AS institution_name,
               sp.slug AS speaker_slug, sp.name AS document_speaker,
               m.provider AS media_provider, m.provider_id AS media_id
          FROM segments s
          JOIN documents d ON d.id = s.document_id
          JOIN institutions i ON i.id = d.institution_id
          LEFT JOIN speakers sp ON sp.id = d.speaker_id
          LEFT JOIN media m ON m.document_id = d.id
         WHERE s.id = ANY(%s)
        """,
        (segment_ids,),
    ).fetchall()
    return {
        int(r["id"]): Passage(
            segment_id=int(r["id"]),
            document_id=int(r["document_id"]),
            position=int(r["position"]),
            institution=r["institution"],
            institution_name=r["institution_name"],
            kind=r["kind"],
            lang=r["lang"],
            title=r["title"],
            published_at=r["published_at"],
            source_url=r["source_url"],
            text=r["text"],
            speaker=r["speaker"],
            speaker_slug=r["speaker_slug"],
            document_speaker=r["document_speaker"],
            start_sec=r["start_sec"],
            end_sec=r["end_sec"],
            media_provider=r["media_provider"],
            media_id=r["media_id"],
        )
        for r in rows
    }
