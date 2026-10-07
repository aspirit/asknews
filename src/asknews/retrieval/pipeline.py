"""The retrieval entry point."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from asknews.db.connection import Connection
from asknews.domain_config import SpeakerDirectory
from asknews.embed.base import Embedder
from asknews.retrieval import candidates as paths
from asknews.retrieval.fusion import Citation, fuse, merge_candidates
from asknews.retrieval.gate import Vocabulary, mentions_domain
from asknews.retrieval.query import Query, analyse

# Without a domain mention, the question is still answered when lexical
# search finds passages matching this many distinct terms or a quoted phrase.
STRONG_LEXICAL_TERMS = 2


@dataclass(frozen=True, slots=True)
class Retrieval:
    query: Query
    citations: list[Citation]
    in_domain: bool
    meta: dict[str, Any] = field(default_factory=dict)


def retrieve(
    conn: Connection,
    embedder: Embedder,
    question: str,
    *,
    directory: SpeakerDirectory,
    vocab: Vocabulary,
) -> Retrieval:
    query = analyse(question, directory, vocab=vocab)
    named = mentions_domain(query, vocab, directory)
    found = paths.lexical(conn, query)
    lexical = found.candidates
    semantic = paths.semantic(conn, embedder, query) if (named or lexical) else []
    merged = merge_candidates(lexical, semantic)
    passages = paths.hydrate(conn, [c.segment_id for c in merged])
    citations = fuse(merged, passages, query)

    strongest_terms = max((c.scores.get("terms", 0.0) for c in lexical), default=0.0)
    phrase_hits = any("phrase" in c.ranks for c in lexical)
    # A quoted phrase found in the archive, a passage containing every content
    # term, or several Japanese terms in one passage: the question is about the
    # archive even if it names nothing in the vocabulary.
    in_domain = (
        named
        or phrase_hits
        or strongest_terms >= STRONG_LEXICAL_TERMS
        or found.strict_fulltext >= 1
    )
    meta = {
        "lang": query.lang,
        "terms": list(query.terms),
        "phrases": list(query.phrases),
        "institutions": list(query.institutions),
        "speakers": list(query.speakers),
        "window": (
            {
                "start": query.window.start.isoformat(),
                "end": query.window.end.isoformat(),
                "hard": query.window.hard,
            }
            if query.window
            else None
        ),
        "named_domain": named,
        "lexical": len(lexical),
        "strict_fulltext": found.strict_fulltext,
        "semantic": len(semantic),
    }
    return Retrieval(query, citations if in_domain else [], in_domain, meta)
