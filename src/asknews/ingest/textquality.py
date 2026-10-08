"""Whether extracted text is readable.

Two things arrive looking like text and are not: a PDF whose text layer
cannot be decoded, which comes out as letters from scripts the corpus
never uses, and a file read as text, which comes out as bytes. Either one
indexed as a document is worse than no document: it is retrieved, shown
and summarised as noise.
"""

from __future__ import annotations

import re
import unicodedata

# Control characters that carry no text: everything but newline and tab.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")
MIN_LETTERS = 50
# meta["text"] of a document whose file yielded no readable text: it is kept
# as a dated link to the original, with no body and no passages.
UNREADABLE = "unusable"
# Of the letters in readable English or Japanese text, this share belongs to
# the Latin, Greek, kana or Han ranges; a broken text layer scatters letters
# across every script there is.
EXPECTED_SHARE = 0.9


def strip_control(text: str) -> str:
    """Drop control characters a text layer may leak, keeping line structure."""
    return _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n\n"))


def _expected_script(char: str) -> bool:
    point = ord(char)
    return (
        point < 0x0250  # Latin, with its extensions
        or 0x0370 <= point <= 0x03FF  # Greek, in formulas
        or 0x3040 <= point <= 0x30FF  # hiragana and katakana
        or 0x3400 <= point <= 0x9FFF  # Han
        or 0xF900 <= point <= 0xFAFF  # Han compatibility
        or 0xFF00 <= point <= 0xFFEF  # full-width forms
    )


def readable(text: str) -> bool:
    """True when the text is prose in one of the corpus languages, long
    enough to carry content, and not a file's bytes."""
    stripped = text.strip()
    if not stripped or stripped.startswith("%PDF"):
        return False
    junk = len(_CONTROL.findall(text)) + text.count(chr(0xFFFD))  # replacement characters
    if junk > max(3, len(text) * 0.005):
        return False
    letters = [c for c in stripped if unicodedata.category(c).startswith("L")]
    if len(letters) < MIN_LETTERS:
        return False
    return sum(1 for c in letters if _expected_script(c)) / len(letters) >= EXPECTED_SHARE
