from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from asknews.embed.base import DIMS
from asknews.embed.client import RemoteEmbedder


def _service(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/info":
        return httpx.Response(200, json={"model": "test-model", "dims": DIMS})
    payload = json.loads(request.content)
    vectors = [[1.0] + [0.0] * (DIMS - 1) for _ in payload["texts"]]
    if payload["kind"] == "query":
        vectors = [[0.0, 1.0] + [0.0] * (DIMS - 2) for _ in payload["texts"]]
    return httpx.Response(200, json={"model": "test-model", "dims": DIMS, "vectors": vectors})


def _embedder(handler: Callable[[httpx.Request], httpx.Response] = _service) -> RemoteEmbedder:
    embedder = RemoteEmbedder("http://embed.test")
    embedder._client = httpx.Client(
        base_url="http://embed.test", transport=httpx.MockTransport(handler)
    )
    return embedder


def test_remote_embedder_reports_model_and_distinguishes_queries() -> None:
    embedder = _embedder()
    assert embedder.model_name == "test-model"
    documents = embedder.embed_documents(["a", "b"])
    assert len(documents) == 2
    assert documents[0][0] == 1.0
    assert embedder.embed_query("q")[1] == 1.0


def test_remote_embedder_rejects_wrong_dimensions() -> None:
    def short(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "m", "dims": 3, "vectors": [[1.0, 0.0, 0.0]]})

    with pytest.raises(ValueError, match="dimensions"):
        _embedder(short).embed_query("q")
