from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from asknews.db.connection import Connection
from asknews.db.migrate import migrate
from asknews.embed import service
from asknews.embed.base import DIMS, HashEmbedder, to_pgvector
from asknews.embed.worker import embed_pending, pending_segments
from asknews.ingest.store import Document, Segment, replace_segments, upsert_document


def test_hash_embedder_is_deterministic_unit_length_and_lexical() -> None:
    embedder = HashEmbedder()
    a = embedder.embed_query("policy rate decision")
    b = embedder.embed_query("policy rate decision")
    c = embedder.embed_query("rainfall in Kyoto")
    assert a == b
    assert len(a) == DIMS
    assert math.isclose(sum(x * x for x in a), 1.0, rel_tol=1e-6)
    dot = sum(x * y for x, y in zip(a, c, strict=True))
    related = sum(x * y for x, y in zip(a, embedder.embed_query("the rate decision"), strict=True))
    assert related > dot


def test_pgvector_literal() -> None:
    assert to_pgvector([0.5, -1.0, 0.25]) == "[0.5,-1,0.25]"


def test_service_embeds_with_injected_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "embedder", HashEmbedder)
    client = TestClient(service.app)
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/info").json() == {"model": "hash-v1", "dims": DIMS}
    response = client.post("/embed", json={"texts": ["a", "b"], "kind": "document"})
    payload = response.json()
    assert (payload["model"], payload["dims"], len(payload["vectors"])) == ("hash-v1", DIMS, 2)
    assert client.post("/embed", json={"texts": []}).status_code == 422


@pytest.mark.integration
def test_worker_embeds_pending_segments_once(db: Connection) -> None:
    from datetime import UTC, datetime

    migrate(db)
    doc_id = upsert_document(
        db,
        Document(
            "fed", "statement", "en", "x", "t", datetime(2026, 1, 1, tzinfo=UTC), "https://e/x"
        ),
    )
    replace_segments(db, doc_id, [Segment(0, "rates held"), Segment(1, "inflation elevated")])
    embedder = HashEmbedder()
    assert len(pending_segments(db, embedder.model_name, limit=10)) == 2
    assert embed_pending(db, embedder, batch_size=1) == 2
    assert embed_pending(db, embedder) == 0
    row = db.execute("SELECT count(*) AS n, min(model) AS model FROM segment_embeddings").fetchone()
    assert row is not None
    assert (row["n"], row["model"]) == (2, "hash-v1")
    # a segment rewrite drops its vector and makes it pending again
    replace_segments(db, doc_id, [Segment(0, "rates held"), Segment(1, "inflation easing")])
    assert len(pending_segments(db, embedder.model_name, limit=10)) == 2
    # the language trigger filled in the new column
    langs = {r["lang"] for r in db.execute("SELECT lang FROM segments")}
    assert langs == {"en"}
