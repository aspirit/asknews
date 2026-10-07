from asknews.cli import build_parser
from asknews.jobs import run_steps, steps


def test_daily_plan_is_ordered_and_runnable() -> None:
    plan = steps(2026, summaries=10)
    labels = [label for label, _ in plan]
    assert labels[:2] == ["fed press conferences", "fed releases"]
    assert labels.index("embed") < labels.index("daily briefs")
    parser = build_parser()
    for _, argv in plan:
        parser.parse_args(argv)  # every step is a valid CLI invocation


def test_run_steps_continues_after_failures() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> int:
        calls.append(argv)
        if argv[0] == "embed":
            raise RuntimeError("boom")
        return 0 if argv[0] != "briefs" else 1

    results = run_steps([("a", ["ingest"]), ("b", ["embed"]), ("c", ["briefs"])], runner=runner)
    assert [code for _, code in results] == [0, 1, 1]
    assert len(calls) == 3
