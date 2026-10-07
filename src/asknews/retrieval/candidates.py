"""Candidate passages from the two retrieval paths and their hydration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from asknews.db.connection import Connection
from asknews.embed.base import Embedder, to_pgvector
from asknews.retrieval.query import Query

LEXICAL_LIMIT = 60
SEMANTIC_LIMIT = 40
HNSW_EF_SEARCH = 100


@dataclass(slots=True)
class Candidate:
    segment_id: int
    scores: dict[str, float] = field(default_factory=dict)  # path name -> raw score
    ranks: dict[str, int] = field(default_factory=dict)  # path name -> 1-based rank


def lexical(conn: Connection, query: Query) -> list[Candidate]:
    """Full-text search for English, term and phrase matching for Japanese.

    Quoted phrases are matched exactly in any language and outrank everything
    else: a user who quotes something wants that wording.
    """
    found: dict[int, Candidate] = {}

    def add(path: str, rows: list[dict[str, Any]]) -> None:
        for rank, row in enumerate(rows, start=1):
            cand = found.setdefault(int(row["id"]), Candidate(int(row["id"])))
            cand.scores[path] = float(row["score"])
            cand.ranks.setdefault(path, rank)

    if query.phrases:
        conditions = " AND ".join("s.text ILIKE %s" for _ in query.phrases)
        rows = conn.execute(
            f"SELECT s.id, {len(query.phrases)}::float AS score FROM segments s "
            f"WHERE {conditions} ORDER BY s.id DESC LIMIT %s",
            [f"%{p}%" for p in query.phrases] + [LEXICAL_LIMIT],
        ).fetchall()
        add("phrase", rows)

    if query.lang == "en":
        rows = conn.execute(
            """
            SELECT s.id, ts_rank_cd(s.tsv, q) AS score
              FROM segments s, websearch_to_tsquery('english', %s) q
             WHERE s.lang = 'en' AND s.tsv @@ q
             ORDER BY score DESC
             LIMIT %s
            """,
            (query.text, LEXICAL_LIMIT),
        ).fetchall()
        add("fulltext", rows)
    elif query.terms:
        terms = list(query.terms[:8])
        hits = " + ".join("(s.text LIKE %s)::int" for _ in terms)
        rows = conn.execute(
            f"""
            SELECT id, score FROM (
                SELECT s.id, ({hits}) AS score
                  FROM segments s
                 WHERE s.lang <> 'en' AND ({" OR ".join("s.text LIKE %s" for _ in terms)})
            ) matched
             WHERE score > 0
             ORDER BY score DESC, id DESC
             LIMIT %s
            """,
            [f"%{t}%" for t in terms] * 2 + [LEXICAL_LIMIT],
        ).fetchall()
        add("terms", rows)
    return list(found.values())


def semantic(conn: Connection, embedder: Embedder, query: Query) -> list[Candidate]:
    vector = to_pgvector(embedder.embed_query(query.text))
    with conn.transaction():
        conn.execute(f"SET LOCAL hnsw.ef_search = {HNSW_EF_SEARCH}")
        rows = conn.execute(
            """
            SELECT e.segment_id AS id, 1 - (e.embedding <=> %s::vector) AS score
              FROM segment_embeddings e
             WHERE e.model = %s
             ORDER BY e.embedding <=> %s::vector
             LIMIT %s
            """,
            (vector, embedder.model_name, vector, SEMANTIC_LIMIT),
        ).fetchall()
    out: list[Candidate] = []
    for rank, row in enumerate(rows, start=1):
        out.append(Candidate(int(row["id"]), {"semantic": float(row["score"])}, {"semantic": rank}))
    return out


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
