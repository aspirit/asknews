import pytest

from asknews.cli import build_parser, main


def test_parser_knows_its_commands() -> None:
    parser = build_parser()
    args = parser.parse_args(["ingest", "fed-presconf", "--limit", "3", "--since", "2020"])
    assert (args.command, args.source, args.limit, args.since) == (
        "ingest",
        "fed-presconf",
        3,
        2020,
    )


def test_unknown_command_exits_with_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["frobnicate"])
    assert exc.value.code == 2
