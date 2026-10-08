from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import ValidationError

from asknews.answer.llm import ScriptedLLM
from asknews.answer.pipeline import Limits
from asknews.api.app import create_app
from asknews.api.deps import Runtime
from asknews.cli import build_parser
from asknews.db.connection import Connection
from asknews.demo import DEFAULT_DSN, Fixture, load_fixture, run, seed, validate_destination
from asknews.domain_config import load_speakers
from asknews.embed.base import HashEmbedder
from asknews.evaluation import evaluate, load_cases
from asknews.ingest.store import upsert_document
from asknews.retrieval.gate import load_vocabulary
from asknews.settings import Settings
from tests.ingest.test_store import sample_document

DOMAIN = Path(__file__).resolve().parents[1] / "domain"


def test_fixture_is_explicitly_synthetic_and_validated() -> None:
    fixture = load_fixture(DOMAIN / "demo.yaml")
    assert len(fixture.documents) == 6
    assert {d.lang for d in fixture.documents} == {"en", "ja"}
    raw = fixture.model_dump()
    raw["documents"].append(raw["documents"][0])
    with pytest.raises(ValidationError, match="duplicate demo document"):
        Fixture.model_validate(raw)
    raw = fixture.model_dump()
    raw["documents"][0]["title"] = "Unlabelled"
    with pytest.raises(ValidationError, match="title"):
        Fixture.model_validate(raw)
    raw = fixture.model_dump()
    raw["documents"][0]["segments"][0]["end_sec"] = 5
    with pytest.raises(ValidationError, match="end_sec"):
        Fixture.model_validate(raw)


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://asknews@remote.example/asknews_demo",
        "postgresql://asknews@127.0.0.1/asknews",
        "postgresql:///asknews_demo",
        "postgresql://asknews@127.0.0.1/asknews_demo?hostaddr=192.0.2.1",
        "postgresql://asknews@127.0.0.1/asknews_demo?service=other",
    ],
)
def test_destination_guard(dsn: str) -> None:
    validate_destination(DEFAULT_DSN)
    with pytest.raises(ValueError, match="local asknews_demo"):
        validate_destination(dsn)
    assert run(build_parser().parse_args(["demo-seed", "--dsn", dsn])) == 2


@pytest.mark.integration
def test_demo_seed_is_repeatable_and_refuses_another_corpus(db: Connection) -> None:
    fixture = load_fixture(DOMAIN / "demo.yaml")
    ids = seed(db, fixture, domain=DOMAIN)
    before = db.execute("SELECT * FROM segment_embeddings ORDER BY segment_id").fetchall()
    assert before
    assert seed(db, fixture, domain=DOMAIN) == ids
    assert db.execute("SELECT * FROM segment_embeddings ORDER BY segment_id").fetchall() == before
    assert db.execute("SELECT count(*) AS n FROM documents").fetchone() == {"n": 6}
    upsert_document(db, replace(sample_document(), external_id="not-demo"))
    with pytest.raises(ValueError, match="another corpus"):
        seed(db, fixture, domain=DOMAIN)
    assert db.execute("SELECT count(*) AS n FROM documents").fetchone() == {"n": 7}


@pytest.mark.integration
def test_demo_routes_answers_and_evidence(db: Connection, test_database_url: str) -> None:
    ids = seed(db, load_fixture(DOMAIN / "demo.yaml"), domain=DOMAIN)
    directory = load_speakers(DOMAIN / "speakers.yaml")
    vocab = load_vocabulary(DOMAIN / "gate.yaml")
    outcomes = evaluate(
        db, HashEmbedder(), load_cases(DOMAIN / "demo_eval.yaml"), directory=directory, vocab=vocab
    )
    assert all(o.passed for o in outcomes), outcomes
    pool: ConnectionPool = ConnectionPool(
        test_database_url,
        min_size=1,
        max_size=2,
        kwargs={"row_factory": dict_row, "autocommit": True},
        open=True,
    )
    settings = Settings(_env_file=None, DATABASE_URL=test_database_url)  # type: ignore[call-arg]
    llm = ScriptedLLM()
    runtime = Runtime(settings, pool, HashEmbedder(), llm, directory, vocab, Limits())
    with TestClient(create_app(runtime)) as client:
        for prefix in ("", "/ja"):
            for path in ("/", "/archive", "/archive/latest", "/archive/meetings", "/ask"):
                assert client.get(prefix + path).status_code == 200
            for doc_id in ids.values():
                page = client.get(f"{prefix}/doc/{doc_id}")
                assert page.status_code == 200
                assert "[Demo]" in page.text
        unreadable = next(v for k, v in ids.items() if "unreadable" in k)
        english = client.get(f"/doc/{unreadable}").text
        assert "could not be read from the publisher" in english
        japanese = client.get(f"/ja/doc/{unreadable}").text
        assert "\u8aad\u307f\u53d6\u308c\u307e\u305b\u3093" in japanese
        answered = client.post("/api/ask", json={"q": "Fed inflation September 2025"})
        assert answered.status_code == 200
        assert answered.json()["status"] == "answered"
        assert answered.json()["citations"]
        for question, status in [
            ("Fed policy in 2001", "no_sources"),
            ("Bake a birthday cake", "declined"),
        ]:
            response = client.post("/api/ask", json={"q": question})
            assert response.json()["status"] == status
        assert len(llm.calls) == 1
