"""A small WebVTT reader.

Only what caption files from the sources use is supported: an optional
header block, cues with ``start --> end`` lines (settings after the end
time are ignored), multi-line cue text, and inline tags such as ``<c>``.

Runs of two or more spaces inside a cue are kept as exactly two. Caption
encoders leave a double space where they dropped a glyph they could not
carry, and a caller holding the published transcript can fill the gap
(see ``asknews.ingest.fed.captions``).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

_TIMING = re.compile(
    r"^(?P<start>\d{1,2}:\d{2}(?::\d{2})?(?:\.\d{1,3})?)\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?(?:\.\d{1,3})?)"
)
_TAG = re.compile(r"<[^>]+>")
_GAP = re.compile(r" {2,}")
_OTHER_SPACE = re.compile(r"[^\S ]+")  # tabs, newlines, no-break spaces
_LINE_MARKER = re.compile(r"^\s*(?:-|>>)\s+")  # speaker-change dashes some caption files use


class VttError(ValueError):
    """The text is not a WebVTT file this reader understands."""


@dataclass(frozen=True, slots=True)
class Cue:
    start: float
    end: float
    text: str


def parse_timestamp(value: str) -> float:
    """Convert ``mm:ss.mmm`` or ``hh:mm:ss.mmm`` to seconds."""
    parts = value.strip().split(":")
    if len(parts) not in (2, 3):
        raise VttError(f"bad timestamp: {value!r}")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def parse_vtt(text: str) -> list[Cue]:
    """Return the cues of a WebVTT document in order, with clean single-line text."""
    body = text.lstrip(chr(0xFEFF))  # a UTF-8 byte order mark, if present
    if not body.startswith("WEBVTT"):
        raise VttError("missing WEBVTT header")
    cues: list[Cue] = []
    for block in re.split(r"\r?\n\r?\n+", body.strip()):
        lines = block.strip().splitlines()
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue  # header, NOTE or STYLE block
        match = _TIMING.match(lines[timing_index].strip())
        if match is None:
            raise VttError(f"bad timing line: {lines[timing_index]!r}")
        raw = " ".join(_LINE_MARKER.sub("", line).strip() for line in lines[timing_index + 1 :])
        clean = _GAP.sub("  ", _OTHER_SPACE.sub(" ", html.unescape(_TAG.sub("", raw)))).strip()
        if clean:
            cues.append(Cue(parse_timestamp(match["start"]), parse_timestamp(match["end"]), clean))
    return cues
