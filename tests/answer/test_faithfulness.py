from asknews.answer.faithfulness import check, parse_reply


def test_parse_json_reply_and_fallback() -> None:
    paragraphs, followups = parse_reply(
        'Sure. {"paragraphs": ["A [1].", "B [2]."], "followups": ["x", "y", "z", "w"]}'
    )
    assert paragraphs == ["A [1].", "B [2]."]
    assert followups == ["x", "y", "z"]
    assert parse_reply("Plain text.\n\nSecond paragraph.") == (
        ["Plain text.", "Second paragraph."],
        [],
    )


def test_check_removes_bad_citations_and_drops_uncited_paragraphs() -> None:
    reply = (
        '{"paragraphs": ["Rates were cut [1][7].", "No citation here.", '
        '"Inflation eased [2] ."], "followups": []}'
    )
    result = check(reply, source_count=3)
    assert result.paragraphs == ["Rates were cut [1].", "Inflation eased [2]."]
    assert result.used == [1, 2]
    assert (result.dropped_paragraphs, result.removed_citations) == (1, 1)
    assert result.is_grounded


def test_check_keeps_a_lone_uncited_refusal_paragraph() -> None:
    result = check('{"paragraphs": ["The sources do not address this."], "followups": []}', 5)
    assert result.paragraphs == ["The sources do not address this."]
    assert result.used == []
    assert not result.is_grounded
