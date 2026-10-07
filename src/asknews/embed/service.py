"""HTTP service that serves the embedding model.

Runs in its own container so the web application never loads model weights::

    uvicorn asknews.embed.service:app --host 0.0.0.0 --port 8201
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field

from asknews.embed.base import DIMS, Embedder

app = FastAPI(title="asknews embedding service", docs_url=None, redoc_url=None)


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=256)
    kind: Literal["query", "document"] = "document"


class EmbedResponse(BaseModel):
    model: str
    dims: int
    vectors: list[list[float]]


@lru_cache(maxsize=1)
def embedder() -> Embedder:
    from asknews.embed.local import LocalEmbedder
    from asknews.settings import get_settings

    return LocalEmbedder(get_settings().embed_model)


@app.get("/info")
def info() -> dict[str, str | int]:
    return {"model": embedder().model_name, "dims": DIMS}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/embed", response_model=EmbedResponse)
def embed(request: EmbedRequest) -> EmbedResponse:
    model = embedder()
    if request.kind == "query":
        vectors = [model.embed_query(t) for t in request.texts]
    else:
        vectors = model.embed_documents(request.texts)
    return EmbedResponse(model=model.model_name, dims=DIMS, vectors=vectors)
