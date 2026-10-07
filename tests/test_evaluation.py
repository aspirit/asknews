from pathlib import Path

from asknews.evaluation import load_cases, matches

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
