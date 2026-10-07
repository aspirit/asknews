"""Response models for the JSON endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from asknews.archive.documents import DocumentSummary
from asknews.retrieval.fusion import Citation


class MediaOut(BaseModel):
    provider: str
    id: str
    duration_sec: int | None = None


class DocumentOut(BaseModel):
    id: int
    institution: str
    institution_name: str
    kind: str
    lang: str
    title: str
    title_translated: bool = False
    published_at: datetime
    source_url: str
    summary: str | None = None
    speaker: str | None = None
    speaker_role: str | None = None
    media: MediaOut | None = None
    timestamped: bool = False

    @classmethod
    def from_summary(cls, doc: DocumentSummary, lang: str) -> DocumentOut:
        title, translated = doc.title_in(lang)
        return cls(
            id=doc.id,
            institution=doc.institution,
            institution_name=doc.institution_name,
            kind=doc.kind,
            lang=doc.lang,
            title=title,
            title_translated=translated,
            published_at=doc.published_at,
            source_url=doc.source_url,
            summary=doc.summary_in(lang),
            speaker=doc.speaker,
            speaker_role=doc.speaker_role,
            media=MediaOut(
                provider=doc.media_provider, id=doc.media_id, duration_sec=doc.duration_sec
            )
            if doc.media_provider and doc.media_id
            else None,
            timestamped=doc.timestamped,
        )


class SegmentOut(BaseModel):
    id: int
    position: int
    text: str
    speaker: str | None = None
    start_sec: float | None = None
    end_sec: float | None = None


class DocumentDetailOut(BaseModel):
    document: DocumentOut
    segments: list[SegmentOut]
    meeting_key: str | None = None


class CitationOut(BaseModel):
    n: int
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
    speaker: str | None
    text: str
    start_sec: float | None
    end_sec: float | None
    media: MediaOut | None
    url: str

    @classmethod
    def from_citation(cls, n: int, citation: Citation) -> CitationOut:
        p = citation.passage
        url = f"/doc/{p.document_id}"
        url += f"?t={int(p.start_sec)}" if p.start_sec is not None else f"#s{p.position + 1}"
        return cls(
            n=n,
            segment_id=p.segment_id,
            document_id=p.document_id,
            position=p.position,
            institution=p.institution,
            institution_name=p.institution_name,
            kind=p.kind,
            lang=p.lang,
            title=p.title,
            published_at=p.published_at,
            source_url=p.source_url,
            speaker=p.speaker or p.document_speaker,
            text=citation.snippet,
            start_sec=p.start_sec,
            end_sec=p.end_sec,
            media=MediaOut(provider=p.media_provider, id=p.media_id)
            if p.media_provider and p.media_id
            else None,
            url=url,
        )


class AskOut(BaseModel):
    status: str
    lang: str
    reason: str | None = None
    paragraphs: list[str] = []
    followups: list[str] = []
    citations: list[CitationOut] = []
    request_id: int | None = None


class MeetingPartOut(BaseModel):
    role: str
    document: DocumentOut


class MeetingOut(BaseModel):
    key: str
    institution: str
    date: str
    decision: str | None
    parts: list[MeetingPartOut]


class SpeakerRankOut(BaseModel):
    slug: str
    name: str
    role: str | None
    institution: str
    speeches: int
    press_conferences: int
    minutes_spoken: int


class InstitutionRankOut(BaseModel):
    code: str
    name: str
    total: int
    by_kind: dict[str, int]


class TopicRankOut(BaseModel):
    slug: str
    label: str
    documents: int
    change: int


class RankingsOut(BaseModel):
    days: int
    speakers: list[SpeakerRankOut]
    institutions: list[InstitutionRankOut]
    topics: list[TopicRankOut]


class BriefOut(BaseModel):
    id: int
    kind: str
    date: str
    meeting_key: str | None
    title: str
    points: list[dict[str, object]]
    institutions: list[str]
    generated_at: datetime
    model: str
