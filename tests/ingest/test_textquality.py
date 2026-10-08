from __future__ import annotations

from asknews.ingest.textquality import readable, strip_control

ENGLISH = (
    "The Committee decided to maintain the target range for the federal funds rate "
    "at 4 to 4-1/4 percent and will continue to monitor incoming information."
)
JAPANESE = (
    "日本銀行は、本日の政策委員会・金融政策決定会合において、次回会合までの金融市場調節方針を決定した。"
    * 2
)
# What pypdf makes of a text layer it cannot decode: letters from scripts the
# corpus never uses, one per glyph.
GARBLED = "ߦۜҕһձ߹ࢫ෼ʣ๏ୈ ৚͠ɺఆձͰঝೝ͞Εͨ΋ͷͰ͋Δɻ" * 4


def test_prose_in_either_language_is_readable() -> None:
    assert readable(ENGLISH)
    assert readable(JAPANESE)
    assert readable(ENGLISH + " \x01 " + JAPANESE)  # one stray control character is tolerated


def test_broken_text_layers_and_raw_files_are_not() -> None:
    assert not readable(GARBLED)
    assert not readable("%PDF-1.6%��� 27 0 obj <> endobj 53 0 obj <>/Encrypt 28 0 R")
    assert not readable("Short.")
    assert not readable("")
    assert not readable("\x01\x02\x03" * 20 + ENGLISH)  # bytes read as text


def test_strip_control_keeps_line_structure() -> None:
    assert strip_control("a\x01b\r\nc\fd\te") == "ab\nc\n\nd\te"
