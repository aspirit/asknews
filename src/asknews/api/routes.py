"""JSON endpoints."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from asknews.answer.pipeline import ask
from asknews.api import schemas
from asknews.api.deps import Runtime, client_hash, connection, runtime
from asknews.archive import briefs as brief_queries
from asknews.archive import documents, meetings, rankings
from asknews.archive.documents import KINDS
from asknews.db.connection import Connection
from asknews.retrieval import retrieve

router = APIRouter(prefix="/api")
Lang = Literal["en", "ja"]
Conn = Annotated[Connection, Depends(connection)]
Rt = Annotated[Runtime, Depends(runtime)]
Question = Annotated[str, Query(min_length=2, max_length=500)]


def _meeting_out(meeting: meetings.Meeting, lang: str) -> schemas.MeetingOut:
    parts = [
        schemas.MeetingPartOut(
            role=part.role,
            status=part.status,
            document=schemas.DocumentOut.from_summary(part.document, lang)
            if part.document is not None
            else None,
        )
        for part in meeting.parts
    ]
    decision = meeting.decision
    return schemas.MeetingOut(
        key=meeting.key,
        institution=meeting.institution,
        date=meeting.date.isoformat(),
        decision=schemas.DecisionOut(
            text=decision.text,
            range=decision.range_text,
            action=decision.action,
            change_bp=decision.change_bp,
            unanimous=decision.unanimous,
            dissenters=list(decision.dissenters_in(lang)),
            votes=decision.votes,
        )
        if decision
        else None,
        parts=parts,
    )


@router.post("/ask", response_model=schemas.AskOut)
def ask_endpoint(request: Request, conn: Conn, rt: Rt, body: schemas.AskIn) -> schemas.AskOut:
    """Answer a question. A POST, so that a crawler following links can never
    run one; the page sends it when the visitor presses Ask."""
    result = ask(
        conn,
        rt.embedder,
        rt.llm,
        body.q,
        directory=rt.directory,
        vocab=rt.vocab,
        client_hash=client_hash(request, rt.settings.client_salt),
        limits=rt.limits,
    )
    return schemas.AskOut(
        status=result.status,
        lang=result.lang,
        reason=result.reason,
        paragraphs=result.paragraphs,
        followups=result.followups,
        citations=[
            schemas.CitationOut.from_citation(i, c) for i, c in enumerate(result.citations, start=1)
        ],
        request_id=result.request_id,
    )


@router.get("/ask/citations", response_model=list[schemas.CitationOut])
def citations_endpoint(conn: Conn, rt: Rt, q: Question) -> list[schemas.CitationOut]:
    found = retrieve(conn, rt.embedder, q, directory=rt.directory, vocab=rt.vocab)
    return [schemas.CitationOut.from_citation(i, c) for i, c in enumerate(found.citations, start=1)]


@router.get("/documents", response_model=list[schemas.DocumentOut])
def documents_endpoint(
    conn: Conn,
    lang: Lang = "en",
    institution: str | None = None,
    kind: str | None = None,
    videos: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[schemas.DocumentOut]:
    if kind is not None and kind not in KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {KINDS}")
    rows = documents.latest(
        conn, institution=institution, kind=kind, with_media=videos, limit=limit, offset=offset
    )
    return [schemas.DocumentOut.from_summary(d, lang) for d in rows]


@router.get("/documents/{document_id}", response_model=schemas.DocumentDetailOut)
def document_endpoint(conn: Conn, document_id: int, lang: Lang = "en") -> schemas.DocumentDetailOut:
    found = documents.detail(conn, document_id)
    if found is None:
        raise HTTPException(status_code=404, detail="document not found")
    return schemas.DocumentDetailOut(
        document=schemas.DocumentOut.from_summary(found.document, lang),
        segments=[
            schemas.SegmentOut(
                id=s.id,
                position=s.position,
                text=s.text,
                speaker=s.speaker,
                start_sec=s.start_sec,
                end_sec=s.end_sec,
            )
            for s in found.segments
        ],
    )


@router.get("/meetings", response_model=list[schemas.MeetingOut])
def meetings_endpoint(
    conn: Conn, lang: Lang = "en", limit: Annotated[int, Query(ge=1, le=100)] = 40
) -> list[schemas.MeetingOut]:
    return [_meeting_out(m, lang) for m in meetings.list_meetings(conn, limit=limit)]


@router.get("/rankings", response_model=schemas.RankingsOut)
def rankings_endpoint(
    conn: Conn, lang: Lang = "en", days: Annotated[int, Query(ge=7, le=3650)] = 90
) -> schemas.RankingsOut:
    return schemas.RankingsOut(
        days=days,
        speakers=[
            schemas.SpeakerRankOut(
                slug=r.slug,
                name=r.name_in(lang),
                role=r.role,
                institution=r.institution,
                speeches=r.speeches,
                press_conferences=r.press_conferences,
                minutes_spoken=r.minutes_spoken,
            )
            for r in rankings.speakers(conn, days=days)
        ],
        institutions=[
            schemas.InstitutionRankOut(code=r.code, name=r.name, total=r.total, by_kind=r.by_kind)
            for r in rankings.institutions(conn)
        ],
        topics=[
            schemas.TopicRankOut(
                slug=r.topic.slug,
                label=r.topic.label.get(lang, r.topic.slug),
                documents=r.documents,
                change=r.change,
            )
            for r in rankings.topic_ranks(conn, days=days)
        ],
    )


@router.get("/briefs", response_model=list[schemas.BriefOut])
def briefs_endpoint(
    conn: Conn, lang: Lang = "en", kind: str | None = None
) -> list[schemas.BriefOut]:
    return [_brief_out(b, lang) for b in brief_queries.list_briefs(conn, kind=kind)]


@router.get("/briefs/{brief_id}", response_model=schemas.BriefOut)
def brief_endpoint(conn: Conn, brief_id: int, lang: Lang = "en") -> schemas.BriefOut:
    brief = brief_queries.get_brief(conn, brief_id)
    if brief is None:
        raise HTTPException(status_code=404, detail="brief not found")
    return _brief_out(brief, lang)


def _brief_out(brief: brief_queries.Brief, lang: str) -> schemas.BriefOut:
    return schemas.BriefOut(
        id=brief.id,
        kind=brief.kind,
        date=brief.brief_date.isoformat(),
        meeting_key=brief.meeting_key,
        title=brief.title_in(lang),
        points=brief.points_in(lang),
        institutions=brief.institutions,
        generated_at=brief.generated_at,
        model=brief.model,
    )
