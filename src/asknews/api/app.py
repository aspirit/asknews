"""Application factory.

Run locally with ``uvicorn asknews.api.app:app --reload``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from asknews import __version__
from asknews.api.deps import Runtime, build_runtime
from asknews.api.routes import router
from asknews.web.routes import WEB_DIR, render
from asknews.web.routes import router as pages


def create_app(runtime: Runtime | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.runtime = runtime or build_runtime()
        try:
            yield
        finally:
            application.state.runtime.close()

    application = FastAPI(
        title="asknews",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    application.include_router(router)
    application.include_router(pages)
    application.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

    @application.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    # Starlette's class, so that unknown paths (raised by the router itself) get the
    # HTML page too, not only 404s raised inside our own routes.
    @application.exception_handler(StarletteHTTPException)
    async def not_found(request: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code == 404 and not request.url.path.startswith("/api/"):
            response = render(request, "404.html", page="")
            response.status_code = 404
            return response
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    return application


app = create_app()
