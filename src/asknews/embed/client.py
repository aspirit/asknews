"""Embedder that calls the embedding service over HTTP."""

from __future__ import annotations

from collections.abc import Sequence

import httpx

from asknews.embed.base import DIMS


class RemoteEmbedder:
    def __init__(self, base_url: str, *, timeout: float = 120.0) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._model_name: str | None = None

    @property
    def model_name(self) -> str:
        if self._model_name is None:
            info = self._client.get("/info").raise_for_status().json()
            self._model_name = str(info["model"])
        return self._model_name

    def _embed(self, texts: Sequence[str], kind: str) -> list[list[float]]:
        response = self._client.post("/embed", json={"texts": list(texts), "kind": kind})
        payload = response.raise_for_status().json()
        self._model_name = str(payload["model"])
        vectors: list[list[float]] = payload["vectors"]
        if any(len(v) != DIMS for v in vectors):
            raise ValueError(f"embedding service returned vectors not of {DIMS} dimensions")
        return vectors

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts, "document")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "query")[0]
