"""Speaker turns and timed segments from caption cues.

Caption tracks published alongside press conference transcripts carry the
transcript text itself, including speaker labels written in capitals
(``CHAIR POWELL.``, ``MICHAEL MCKEE.``). Joining the cues gives the full
text; the cue boundaries give a timestamp for any character offset. That
is enough to cut the text into attributed turns and then into segments of
a bounded size, each with the start and end time of the video it covers.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass

from asknews.ingest.store import Segment
from asknews.ingest.vtt import Cue

# Two to four capitalised words followed by a period or colon.
# Requiring at least two words keeps acronyms at sentence ends ("...the
# FOMC. We ...") from being read as speakers.
_SPEAKER_LABEL = re.compile(
    r"(?:^|(?<=\s))((?:[A-Z][A-Z'.\-]+)(?: [A-Z][A-Z'.\-]+){1,3})[.:]\s+(?=\S)"
)
_SENTENCE_END = re.compile(r"(?:(?<=[.!?])|(?<=[.!?][\"')\]]))\s+(?=[A-Z\"'(\[])")
_MIN_PREFACE_CHARS = 300


@dataclass(frozen=True, slots=True)
class Turn:
    speaker: str | None
    text: str
    start: float
    end: float
    offset: int  # position of the text within the joined caption text


class TimedText:
    """The joined caption text with a time lookup for character offsets."""

    def __init__(self, cues: Sequence[Cue]) -> None:
        pieces: list[str] = []
        self._starts: list[int] = []
        self._cues = list(cues)
        offset = 0
        for cue in self._cues:
            self._starts.append(offset)
            pieces.append(cue.text)
            offset += len(cue.text) + 1
        self.text = " ".join(pieces)

    def cue_at(self, offset: int) -> Cue:
        index = max(0, bisect_right(self._starts, offset) - 1)
        return self._cues[index]

    def start_at(self, offset: int) -> float:
        return self.cue_at(offset).start

    def end_at(self, offset: int) -> float:
        return self.cue_at(max(0, offset - 1)).end


def normalise_speaker(label: str) -> str:
    """``CHAIR POWELL`` -> ``Chair Powell``; keeps Mc/Mac and O' prefixes readable."""
    name = " ".join(word.capitalize() for word in label.split())
    name = re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), name)
    return re.sub(r"\bO'([a-z])", lambda m: "O'" + m.group(1).upper(), name)


def speaker_turns(cues: Sequence[Cue]) -> list[Turn]:
    """Cut the caption text at speaker labels.

    Text before the first label (usually the transcript title) is dropped
    when short and kept as an unattributed turn otherwise.
    """
    timed = TimedText(cues)
    text = timed.text
    labels = list(_SPEAKER_LABEL.finditer(text))
    turns: list[Turn] = []

    def add(speaker: str | None, begin: int, end: int, *, start_offset: int) -> None:
        chunk = text[begin:end].strip()
        if chunk:
            turns.append(
                Turn(speaker, chunk, timed.start_at(start_offset), timed.end_at(end), begin)
            )

    if not labels:
        add(None, 0, len(text), start_offset=0)
        return turns
    if labels[0].start() >= _MIN_PREFACE_CHARS:
        add(None, 0, labels[0].start(), start_offset=0)
    for i, match in enumerate(labels):
        end = labels[i + 1].start() if i + 1 < len(labels) else len(text)
        # The turn starts where its label starts: that is the moment the
        # speaker takes the floor in the captions.
        add(normalise_speaker(match.group(1)), match.end(), end, start_offset=match.start())
    return turns


def segment_cues(cues: Sequence[Cue], *, max_chars: int = 900) -> list[Segment]:
    """Turn captions into ordered, timestamped segments of at most ``max_chars``.

    A segment never spans two speakers. Long turns are split at sentence
    boundaries; a single sentence longer than ``max_chars`` stays whole.
    """
    timed = TimedText(cues)
    segments: list[Segment] = []
    for turn in speaker_turns(cues):
        for begin, end in _sentence_windows(turn.text, max_chars):
            start = turn.start if begin == 0 else timed.start_at(turn.offset + begin)
            segments.append(
                Segment(
                    position=len(segments),
                    text=turn.text[begin:end].strip(),
                    speaker=turn.speaker,
                    start_sec=start,
                    end_sec=timed.end_at(turn.offset + end),
                )
            )
    return segments


def _sentence_windows(text: str, max_chars: int) -> list[tuple[int, int]]:
    """Offsets of windows that pack whole sentences up to ``max_chars``."""
    boundaries = [0, *(m.end() for m in _SENTENCE_END.finditer(text)), len(text)]
    windows: list[tuple[int, int]] = []
    begin = 0
    for i in range(1, len(boundaries)):
        if boundaries[i] - begin > max_chars and boundaries[i - 1] > begin:
            windows.append((begin, boundaries[i - 1]))
            begin = boundaries[i - 1]
    windows.append((begin, len(text)))
    return windows


def window_text(text: str, *, max_chars: int = 900) -> list[str]:
    """Split plain text into sentence-aligned windows of at most ``max_chars``."""
    return [text[b:e].strip() for b, e in _sentence_windows(text, max_chars) if text[b:e].strip()]
