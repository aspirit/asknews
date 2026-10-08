import json
from datetime import date
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from asknews.answer.llm import ScriptedLLM
from asknews.cli import build_parser
from asknews.embed.base import HashEmbedder
from asknews.evaluation import Case, Outcome, evaluate, load_cases, matches, metrics, run
from asknews.retrieval import retrieve
from asknews.retrieval.pipeline import RetrievalMode
from asknews.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_shipped_cases_load() -> None:
    cases = load_cases(REPO_ROOT / "domain" / "eval_cases.yaml")
    assert len(cases) >= 3
    assert all(c.within >= 1 and c.expect for c in cases)


def test_matching_rules() -> None:
    row = {
        "institution": "fed",
        "kind": "minutes",
        "external_id": "20250917",
        "title": "Minutes of the FOMC",
    }
    assert matches({"institution": "fed", "kind": "minutes"}, row)
    assert matches({"title_contains": "fomc"}, row)
    assert not matches({"external_id": "x"}, row)


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"cases": []},
        {"cases": ["not a case"]},
        {"cases": [{"question": "Fed?", "expect": {}}]},
        {"cases": [{"question": "Fed?", "expect": {"typo": "fed"}}]},
        {"cases": [{"question": "Fed?", "expect": {"institution": ""}}]},
        {"cases": [{"question": "Fed?", "expect": {"external_id": 20250917}}]},
        {"cases": [{"question": "Fed?", "outcome": "declined"}]},
        {
            "cases": [
                {"question": "Fed?", "outcome": "no_sources", "expect": {"institution": "fed"}}
            ]
        },
        {"cases": [{"question": "", "outcome": "no_sources"}]},
        {"cases": [{"question": "Fed?", "outcome": "no_sources", "within": 13}]},
        {"cases": [{"question": "Fed?", "outcome": "no_sources", "within": True}]},
        {"cases": [{"question": "Fed?", "outcome": "no_sources", "unknown": 1}]},
        {"cases": [{"question": "Fed?", "outcome": "no_sources", "id": ""}]},
        {"cases": [{"question": "Fed?", "outcome": "no_sources", "id": 3}]},
        {"cases": [{"question": "Fed?", "outcome": "no_sources", "id": "same"}] * 2},
    ],
)
def test_invalid_cases_fail_closed(tmp_path: Path, raw: object) -> None:
    path = tmp_path / "cases.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises((ValueError, TypeError), match=r"case|expect|within|outcome"):
        load_cases(path)


def test_evidence_match_normalises_whitespace_not_content() -> None:
    assert matches({"passage_contains": "lower the target"}, {"text": "LOWER\n the target range"})
    assert not matches({"passage_contains": "lower the target"}, {"text": "keep the target"})
    assert not matches({"passage_contains": "lower the target"}, {"title": "lower the target"})


def test_metrics_separate_negative_cases_and_missing_relevance() -> None:
    positive = Case("Fed?", {"institution": "fed"})
    negative = Case("Other?", {}, outcome="out_of_scope")
    outcomes = [
        Outcome(positive, 2, 4, elapsed_ms=10),
        Outcome(positive, None, 4, elapsed_ms=20),
        Outcome(negative, None, 0, "out_of_scope", 30),
    ]
    assert metrics(outcomes) == {
        "cases": 3,
        "passed": 2,
        "positive_cases": 2,
        "hit_at_5": 0.5,
        "mrr_at_12": 0.25,
        "negative_cases": 1,
        "negative_accuracy": 1.0,
        "p50_ms": 20,
        "p95_ms": 30,
    }
    assert metrics([])["hit_at_5"] is None
    assert not Outcome(negative, None, 0, "no_sources").passed


@pytest.mark.integration
def test_passage_relevance_modes_and_reference_date(
    api: tuple[TestClient, dict[str, int], ScriptedLLM],
) -> None:
    client, _, _ = api
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    cases = [
        Case(
            "Fed inflation September 2025",
            {"institution": "fed", "passage_contains": "Inflation has moved up"},
        ),
        Case(
            "Fed inflation September 2025",
            {"institution": "fed", "passage_contains": "This is absent"},
        ),
        Case("How do I bake a birthday cake?", {}, outcome="out_of_scope"),
        Case("Fed policy in 2001", {}, outcome="no_sources"),
    ]
    with runtime.pool.connection() as conn:
        modes: tuple[RetrievalMode, ...] = ("lexical", "dense", "hybrid")
        for mode in modes:
            outcomes = evaluate(
                conn,
                HashEmbedder(),
                cases,
                directory=runtime.directory,
                vocab=runtime.vocab,
                mode=mode,
                today=date(2025, 10, 9),
            )
            assert [o.passed for o in outcomes] == [True, False, True, True]
            assert all(o.elapsed_ms >= 0 for o in outcomes)


@pytest.mark.integration
def test_lexical_mode_does_not_embed_and_uses_fixed_date(
    api: tuple[TestClient, dict[str, int], ScriptedLLM],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_embedding(text: str) -> list[float]:
        raise AssertionError("lexical mode must not embed")

    client, _, _ = api
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    encoder = HashEmbedder()
    monkeypatch.setattr(encoder, "embed_query", unexpected_embedding)
    with runtime.pool.connection() as conn:
        result = retrieve(
            conn,
            encoder,
            "latest Fed inflation",
            directory=runtime.directory,
            vocab=runtime.vocab,
            mode="lexical",
            today=date(2025, 10, 9),
        )
        assert result.query.window is not None
        assert result.query.window.end == date(2025, 10, 9)
        assert result.citations
        assert all("semantic" not in c.paths for c in result.citations)
        dense = retrieve(
            conn,
            HashEmbedder(),
            "Fed inflation",
            directory=runtime.directory,
            vocab=runtime.vocab,
            mode="dense",
        )
        assert dense.citations
        assert all(set(c.paths) <= {"semantic", "answer"} for c in dense.citations)


@pytest.mark.integration
def test_cli_writes_report_even_when_a_case_fails(
    api: tuple[TestClient, dict[str, int], ScriptedLLM],
    test_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "cases.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "id": "missing",
                        "question": "Fed inflation",
                        "expect": {"external_id": "absent"},
                    },
                    {"id": "refusal", "question": "Bake a cake", "outcome": "out_of_scope"},
                ]
            }
        )
    )
    report = tmp_path / "reports" / "run.json"
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    # A remote embedder nobody is listening for: a lexical run must not call it.
    monkeypatch.setenv("ASKNEWS_EMBEDDER", "remote")
    monkeypatch.setenv("ASKNEWS_EMBED_URL", "http://127.0.0.1:9")
    get_settings.cache_clear()
    try:
        args = build_parser().parse_args(
            [
                "eval",
                "--cases",
                str(path),
                "--mode",
                "lexical",
                "--as-of",
                "2025-10-09",
                "--report",
                str(report),
                "--corpus-label",
                "fixture-v1",
            ]
        )
        assert run(args) == 1
        data = json.loads(report.read_text())
        assert data["as_of"] == "2025-10-09"
        assert data["corpus"]["documents"] == 3
        assert data["embedder"] == "none"  # a lexical run uses no embedding model
        assert data["metrics"]["passed"] == 1
        assert len(data["cases_sha256"]) == 64
        assert test_database_url not in report.read_text()
        path.write_text("cases: []")
        assert run(args) == 2
    finally:
        get_settings.cache_clear()
