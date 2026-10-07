"""Repairs to caption text from the published transcript.

The caption track is the transcript, but its encoder drops glyphs it cannot
carry, leaving a double space where a fraction such as 3 3/4 stood, and it
writes every dash as a hyphen. When the transcript PDF is available, the
same passage is looked up there and the caption takes the transcript's
token; what cannot be matched is left as the captions had it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from asknews.ingest.vtt import Cue

_GAP = re.compile(r"(\S+) {2}(?=\S)")
_HYPHENATED = re.compile(r"\b([A-Za-z]+)-([a-z]+)\b")
_SPACES = re.compile(r"\s+")
_EM_DASH = chr(0x2014)
_DASHES = "[" + chr(0x2013) + chr(0x2014) + "]"


def fill_gaps(text: str, transcript: str) -> str:
    """Replace each double-space gap in ``text`` with what the transcript has there.

    The word before the gap, one more word of context when there is one, and
    the two words after the gap are looked up in the transcript. A character
    glued to the word there (``3`` followed by a fraction glyph) or a separate
    token between the words fills the gap; an unmatched gap becomes a space.
    """
    flat = _SPACES.sub(" ", transcript)

    def fix(match: re.Match[str]) -> str:
        word = match.group(1)
        preceding = text[: match.start(1)].split()[-1:]
        lead = re.escape(" ".join([*preceding, word]))
        trail_words = text[match.end() :].split()[:2]
        if not trail_words:
            return word + " "
        trail = re.escape(" ".join(trail_words))
        glued = re.search(r"(?<!\S)" + lead + r"(\S)\s+" + trail, flat)
        if glued:
            return word + glued.group(1) + " "
        separate = re.search(r"(?<!\S)" + lead + r"\s+(\S+)\s+" + trail, flat)
        if separate:
            return word + " " + separate.group(1) + " "
        return word + " "

    return _GAP.sub(fix, text)


_FRACTIONS = {
    "1/4": chr(0xBC),
    "1/2": chr(0xBD),
    "3/4": chr(0xBE),
    "1/8": chr(0x215B),
    "3/8": chr(0x215C),
    "5/8": chr(0x215D),
    "7/8": chr(0x215E),
}
_RANGE_GAP = re.compile(r"(\d) {2}(?=to (\d[\d\-/]*) percent)")
_STATEMENT_RANGE = re.compile(r"(\d[\d\-/]*) to (\d[\d\-/]*) percent")


def fill_range_gaps(text: str, statement: str) -> str:
    """Fill a gap after a digit in a rate range from the statement's wording.

    Captions drop the fraction glyph in "3 3/4 to 4 percent"; the policy
    statement of the same meeting writes the range as "3-3/4 to 4 percent",
    so the matching range supplies the fraction.
    """
    ranges = {high: low for low, high in _STATEMENT_RANGE.findall(statement)}

    def fix(match: re.Match[str]) -> str:
        digit, high = match.group(1), match.group(2)
        low = ranges.get(high, "")
        whole, _, fraction = low.partition("-")
        if whole == digit and fraction in _FRACTIONS:
            return digit + _FRACTIONS[fraction] + " "
        return digit + " "

    return _RANGE_GAP.sub(fix, text)


def restore_dashes(text: str, transcript: str) -> str:
    """Give back the dash where the captions wrote a hyphen and the transcript has a dash.

    ``jobless-the jobless rate`` becomes ``jobless`` em dash ``the``; a word
    the transcript also hyphenates (``private-sector``) is left alone.
    """
    flat = _SPACES.sub(" ", transcript)
    seen: dict[tuple[str, str], bool] = {}

    def fix(match: re.Match[str]) -> str:
        pair = (match.group(1), match.group(2))
        if pair not in seen:
            pattern = re.escape(pair[0]) + r" ?" + _DASHES + r" ?" + re.escape(pair[1])
            seen[pair] = re.search(pattern, flat) is not None
        return pair[0] + _EM_DASH + pair[1] if seen[pair] else match.group(0)

    return _HYPHENATED.sub(fix, text)


def repair_cues(
    cues: Sequence[Cue], transcript: str | None, statement: str | None = None
) -> list[Cue]:
    """Cues with gaps filled and dashes restored from ``transcript``; without
    one, the meeting's statement can still fill a rate-range gap. Any gap
    left collapses to a single space.
    """
    repaired: list[Cue] = []
    for cue in cues:
        text = cue.text
        if transcript:
            text = restore_dashes(fill_gaps(text, transcript), transcript)
        elif statement:
            text = fill_range_gaps(text, statement)
        repaired.append(Cue(cue.start, cue.end, re.sub(r" {2,}", " ", text).strip()))
    return repaired
