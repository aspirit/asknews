from __future__ import annotations

import pytest

from asknews import jobs
from asknews.cli import main


def test_daily_dry_run_prints_the_plan(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["daily", "--dry-run", "--summaries", "7"]) == 0
    out = capsys.readouterr().out
    assert "fed press conferences" in out
    assert "summarize --limit 7" in out
    assert out.index("ingest fed-presconf") < out.index("embed") < out.index("briefs daily")


def test_daily_only_filters_steps_and_reports_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> int:
        calls.append(argv)
        return 1 if argv[0] == "embed" else 0

    monkeypatch.setattr(jobs, "main", runner, raising=False)
    monkeypatch.setattr("asknews.cli.main", runner)
    assert main(["daily", "--only", "topics", "embed"]) == 1
    assert [c[0] for c in calls] == ["topics", "embed"]
    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "ok" in out
