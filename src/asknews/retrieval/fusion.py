"""Merging the paths into one ranked list of citations."""

from __future__ import annotations

from dataclasses import dataclass

from asknews.retrieval.candidates import Candidate, Passage
from asknews.retrieval.query import Query

RRF_K = 60
MAX_CITATIONS = 12
MAX_PER_DOCUMENT = 2
SNIPPET_CHARS = 420
# The speaker label the Bank of Japan transcript ingester gives reporters'
# questions. A question is retrieved for the words it shares with the user's;
# what the user wants is the answer that follows it.
QUESTION_SPEAKER = "Question"
_QUESTION_PENALTY = 0.01

_PATH_WEIGHT = {"phrase": 2.0, "fulltext": 1.0, "terms": 1.0, "semantic": 1.0}
# The statement is the decision itself; the press conference explains it.
_KIND_WEIGHT = {
    "statement": 0.025,
    "press_conference": 0.015,
    "minutes": 0.010,
    "report": 0.005,
    "speech": 0.0,
    "release": 0.0,
}


@dataclass(frozen=True, slots=True)
class Citation:
    passage: Passage
    score: float
    paths: tuple[str, ...]

    @property
    def snippet(self) -> str:
        text = self.passage.text
        return (
            text if len(text) <= SNIPPET_CHARS else text[: SNIPPET_CHARS - 1].rstrip() + chr(0x2026)
        )


def fused_score(candidate: Candidate, passage: Passage, query: Query) -> float:
    score = sum(
        _PATH_WEIGHT.get(path, 1.0) / (RRF_K + rank) for path, rank in candidate.ranks.items()
    )
    score += _KIND_WEIGHT.get(passage.kind, 0.0)
    if passage.speaker == QUESTION_SPEAKER:
        score -= _QUESTION_PENALTY
    if passage.institution in query.institutions:
        score += 0.02
    if passage.kind in query.kinds:
        score += 0.03  # "the minutes", "the press conference": the kind asked for
    if passage.speaker_slug and passage.speaker_slug in query.speakers:
        score += 0.03
    if (
        query.window is not None
        and query.window.start <= passage.published_at.date() <= query.window.end
    ):
        score += 0.03  # hard windows are already applied in SQL; this rewards soft recency
    return score


def fuse(candidates: list[Candidate], passages: dict[int, Passage], query: Query) -> list[Citation]:
    scored: list[Citation] = []
    for cand in candidates:
        passage = passages.get(cand.segment_id)
        if passage is None:
            continue
        scored.append(
            Citation(passage, fused_score(cand, passage, query), tuple(sorted(cand.ranks)))
        )
    scored.sort(key=lambda c: (-c.score, -c.passage.published_at.timestamp()))
    per_document: dict[int, int] = {}
    out: list[Citation] = []
    for citation in scored:
        count = per_document.get(citation.passage.document_id, 0)
        if count >= MAX_PER_DOCUMENT:
            continue
        per_document[citation.passage.document_id] = count + 1
        out.append(citation)
        if len(out) == MAX_CITATIONS:
            break
    return out


def with_answers(citations: list[Citation], answers: dict[int, Passage]) -> list[Citation]:
    """Place each retrieved question's answer right after it.

    ``answers`` maps a question segment's id to the passage that follows it.
    An answer already in the list is not added twice; the list is cut back to
    ``MAX_CITATIONS`` from the bottom.
    """
    present = {c.passage.segment_id for c in citations}
    out: list[Citation] = []
    for citation in citations:
        out.append(citation)
        answer = answers.get(citation.passage.segment_id)
        if answer is not None and answer.segment_id not in present:
            out.append(Citation(answer, citation.score - 1e-6, ("answer",)))
            present.add(answer.segment_id)
    return out[:MAX_CITATIONS]


def merge_candidates(*groups: list[Candidate]) -> list[Candidate]:
    merged: dict[int, Candidate] = {}
    for group in groups:
        for cand in group:
            target = merged.setdefault(cand.segment_id, Candidate(cand.segment_id))
            target.scores.update(cand.scores)
            for path, rank in cand.ranks.items():
                target.ranks.setdefault(path, rank)
    return list(merged.values())
