"""Page routes. Data comes from the same read modules the JSON API uses."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, TypeVar

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates

from asknews.api.deps import connection
from asknews.archive import briefs as brief_queries
from asknews.archive import documents, meetings, rankings
from asknews.archive.documents import KINDS, DocumentSummary
from asknews.db.connection import Connection
from asknews.settings import get_settings
from asknews.web.assets import asset
from asknews.web.i18n import (
    LANGUAGES,
    Translator,
    duration,
    localized_path,
    path_language,
    prefix,
)

WEB_DIR = Path(__file__).resolve().parent
router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
templates.env.filters["duration"] = duration
templates.env.globals["asset"] = asset
Conn = Annotated[Connection, Depends(connection)]
RANKING_PERIODS = (30, 90, 365, 3650)
F = TypeVar("F", bound=Callable[..., Any])


def language(request: Request) -> str:
    """The interface language is the path prefix: ``/ja/...`` is Japanese, the root English."""
    return path_language(request.url.path)[0]


def page(path: str) -> Callable[[F], F]:
    """Register a page under every language: the default at ``path``, the
    others under their prefix, so each language version has its own URL."""

    def register(endpoint: F) -> F:
        for lang in LANGUAGES:
            router.add_api_route(
                localized_path(path, lang),
                endpoint,
                methods=["GET"],
                response_class=HTMLResponse,
                name=f"{endpoint.__name__}_{lang}",
            )
        return endpoint

    return register


@lru_cache(maxsize=1)
def _domain_lists() -> dict[str, Any]:
    domain = get_settings().domain_dir
    questions = yaml.safe_load((domain / "questions.yaml").read_text("utf-8")) or {}
    featured = yaml.safe_load((domain / "featured_videos.yaml").read_text("utf-8")) or {}
    return {
        "questions": questions.get("questions") or {},
        "document_questions": questions.get("document_questions") or {},
        "featured": featured.get("videos") or [],
    }


def sample_questions(lang: str, *, limit: int = 4) -> list[str]:
    items: list[str] = _domain_lists()["questions"].get(lang) or []
    return items[:limit]


def document_questions(kind: str, lang: str) -> list[str]:
    by_kind: dict[str, dict[str, list[str]]] = _domain_lists()["document_questions"]
    return (by_kind.get(kind) or by_kind.get("statement") or {}).get(lang) or []


def featured_videos(conn: Connection, lang: str) -> list[tuple[DocumentSummary, str]]:
    entries = _domain_lists()["featured"]
    if not entries:
        return []
    rows = conn.execute(
        """
        SELECT d.id, i.code, d.external_id FROM documents d
          JOIN institutions i ON i.id = d.institution_id
         WHERE d.kind = 'press_conference'
           AND (i.code, d.external_id) IN (SELECT unnest(%s::text[]), unnest(%s::text[]))
        """,
        ([e["institution"] for e in entries], [str(e["external_id"]) for e in entries]),
    ).fetchall()
    ids = {(r["code"], r["external_id"]): int(r["id"]) for r in rows}
    found = documents.by_ids(conn, list(ids.values()))
    out: list[tuple[DocumentSummary, str]] = []
    for entry in entries:
        doc_id = ids.get((entry["institution"], str(entry["external_id"])))
        if doc_id in found:
            out.append((found[doc_id], str((entry.get("why") or {}).get(lang) or "")))
    return out


def render(request: Request, template: str, *, page: str, **context: Any) -> HTMLResponse:
    lang, base_path = path_language(request.url.path)
    query = f"?{request.url.query}" if request.url.query else ""
    return templates.TemplateResponse(
        request,
        template,
        {
            "t": Translator(lang),
            "lang": lang,
            "page": page,
            "languages": LANGUAGES,
            "prefix": prefix(lang),
            "u": lambda path: localized_path(path, lang),
            "alternates": {code: localized_path(base_path, code) for code in LANGUAGES},
            "switches": {code: localized_path(base_path + query, code) for code in LANGUAGES},
            "canonical_path": localized_path(base_path, lang),
            "site_url": get_settings().site_url,
            **context,
        },
    )


@page("/")
def home(request: Request, conn: Conn) -> HTMLResponse:
    lang = language(request)
    return render(
        request,
        "home.html",
        page="home",
        latest=sorted(
            documents.prominent(conn, limit=10), key=lambda d: d.published_at, reverse=True
        ),
        featured=featured_videos(conn, lang),
        questions=sample_questions(lang),
    )


@page("/ask")
def ask_page(request: Request, q: str | None = None, doc: int | None = None) -> HTMLResponse:
    lang = language(request)
    return render(
        request,
        "ask.html",
        page="ask",
        question=q or "",
        doc_id=doc,
        questions=sample_questions(lang),
    )


@page("/archive")
def archive_hub(request: Request, conn: Conn) -> HTMLResponse:
    counts = documents.counts(conn)
    counts["briefs"] = len(brief_queries.list_briefs(conn, limit=200))
    counts["meetings"] = len(meetings.list_meetings(conn, limit=400))
    return render(
        request,
        "archive.html",
        page="archive",
        section=None,
        counts=counts,
        latest=documents.latest(conn, limit=6),
    )


@page("/archive/latest")
def archive_latest(
    request: Request, conn: Conn, institution: str | None = None, kind: str | None = None
) -> HTMLResponse:
    if kind is not None and kind not in KINDS:
        raise HTTPException(status_code=404)
    rows = conn.execute("SELECT code FROM institutions ORDER BY code").fetchall()
    return render(
        request,
        "archive_latest.html",
        page="archive",
        section="latest",
        institutions=[r["code"] for r in rows],
        kinds=KINDS,
        institution=institution,
        kind=kind,
        items=documents.latest(conn, institution=institution, kind=kind, limit=20),
    )


@page("/archive/briefs")
def archive_briefs(request: Request, conn: Conn, kind: str | None = None) -> HTMLResponse:
    if kind not in (None, "daily", "meeting"):
        raise HTTPException(status_code=404)
    return render(
        request,
        "archive_briefs.html",
        page="archive",
        section="briefs",
        kind=kind,
        briefs=brief_queries.list_briefs(conn, kind=kind),
    )


@page("/archive/videos")
def archive_videos(request: Request, conn: Conn, institution: str | None = None) -> HTMLResponse:
    items = documents.latest(conn, institution=institution, with_media=True, limit=100)
    return render(
        request,
        "archive_videos.html",
        page="archive",
        section="videos",
        institution=institution,
        institutions=["fed", "boj"],
        items=items,
    )


@page("/archive/rankings")
def archive_rankings(
    request: Request, conn: Conn, days: Annotated[int, Query(ge=7, le=3650)] = 90
) -> HTMLResponse:
    return render(
        request,
        "archive_rankings.html",
        page="archive",
        section="rankings",
        days=days,
        periods=RANKING_PERIODS,
        speakers=rankings.speakers(conn, days=days),
        publishers=rankings.institutions(conn),
        topics=rankings.topic_ranks(conn, days=days),
    )


@page("/archive/meetings")
def archive_meetings(request: Request, conn: Conn, open: str | None = None) -> HTMLResponse:
    found = meetings.list_meetings(conn, limit=60)
    by_month: dict[str, dict[str, meetings.Meeting]] = defaultdict(dict)
    for meeting in found:
        by_month[meeting.date.strftime("%Y-%m")].setdefault(meeting.institution, meeting)
    # Each month shows the date of one of its meetings in the interface language.
    months = [
        (key, next(iter(by_month[key].values())).date) for key in sorted(by_month, reverse=True)
    ]
    return render(
        request,
        "archive_meetings.html",
        page="archive",
        section="meetings",
        months=months,
        by_month=by_month,
        open_key=open or (found[0].key if found else None),
        briefs={
            b.meeting_key: b for b in brief_queries.list_briefs(conn, kind="meeting", limit=200)
        },
    )


@page("/doc/{document_id}")
def document_page(request: Request, conn: Conn, document_id: int) -> HTMLResponse:
    found = documents.detail(conn, document_id)
    if found is None:
        raise HTTPException(status_code=404)
    lang = language(request)
    meeting = next(
        (
            m
            for m in meetings.list_meetings(conn, limit=400)
            if any(d.id == document_id for d in m.documents)
        ),
        None,
    )
    return render(
        request,
        "doc.html",
        page="archive",
        doc=found.document,
        segments=found.segments,
        blocks=documents.speaker_blocks(found.segments),
        meta=found.meta,
        meeting=meeting,
        questions=document_questions(found.document.kind, lang),
        start=request.query_params.get("t"),
    )


@page("/brief/{brief_id}")
def brief_page(request: Request, conn: Conn, brief_id: int) -> HTMLResponse:
    brief = brief_queries.get_brief(conn, brief_id)
    if brief is None:
        raise HTTPException(status_code=404)
    lang = language(request)
    cited_ids = sorted({int(i) for p in brief.points for i in (p.get("documents") or [])})
    cited = documents.by_ids(conn, cited_ids)
    return render(
        request,
        "brief.html",
        page="archive",
        brief=brief,
        points=brief.points_in(lang),
        cited=[cited[i] for i in cited_ids if i in cited],
        meeting=next(
            (m for m in meetings.list_meetings(conn, limit=400) if m.key == brief.meeting_key), None
        )
        if brief.meeting_key
        else None,
    )


ROBOTS = "User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: {site}/sitemap.xml\n"
_XML_ESCAPES = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots() -> str:
    return ROBOTS.format(site=get_settings().site_url)


def sitemap_entries(conn: Connection) -> list[tuple[str, str | None]]:
    """``(path, lastmod)`` for every page worth indexing, in the default language."""
    entries: list[tuple[str, str | None]] = [
        (p, None)
        for p in (
            "/",
            "/ask",
            "/archive",
            "/archive/latest",
            "/archive/briefs",
            "/archive/videos",
            "/archive/rankings",
            "/archive/meetings",
            "/about",
        )
    ]
    documents_rows = conn.execute("SELECT id, updated_at FROM documents ORDER BY id").fetchall()
    entries.extend(
        (f"/doc/{row['id']}", row["updated_at"].date().isoformat()) for row in documents_rows
    )
    brief_rows = conn.execute("SELECT id, generated_at FROM briefs ORDER BY id").fetchall()
    entries.extend(
        (f"/brief/{row['id']}", row["generated_at"].date().isoformat()) for row in brief_rows
    )
    return entries


@router.get("/sitemap.xml")
def sitemap(conn: Conn) -> Response:
    """Every page in every language, each naming its alternates."""
    site = get_settings().site_url
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:xhtml="http://www.w3.org/1999/xhtml">',
    ]
    for path, lastmod in sitemap_entries(conn):
        alternates = "".join(
            f'<xhtml:link rel="alternate" hreflang="{code}" '
            f'href="{(site + localized_path(path, code)).translate(_XML_ESCAPES)}"/>'
            for code in LANGUAGES
        )
        for code in LANGUAGES:
            loc = (site + localized_path(path, code)).translate(_XML_ESCAPES)
            modified = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
            lines.append(f"<url><loc>{loc}</loc>{modified}{alternates}</url>")
    lines.append("</urlset>")
    return Response("\n".join(lines) + "\n", media_type="application/xml")


@page("/about")
def about(request: Request) -> HTMLResponse:
    return render(request, "about.html", page="about")
