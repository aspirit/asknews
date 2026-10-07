"""Application factory.

Run locally with ``uvicorn asknews.api.app:app --reload``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from asknews import __version__
from asknews.api.deps import Runtime, build_runtime
from asknews.api.routes import router


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
        redoc_url=None,
    )
    application.include_router(router)

    @application.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return application


app = create_app()
