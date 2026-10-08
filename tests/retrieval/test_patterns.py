from __future__ import annotations

from asknews.retrieval.candidates import _contains


def test_like_patterns_keep_wildcards_literal() -> None:
    assert _contains("2%") == "%2\\%%"
    assert _contains("a_b") == "%a\\_b%"
    assert _contains("plain") == "%plain%"
