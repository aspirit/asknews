"""Checking the model's reply against the sources it was given."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

_CITATION = re.compile(r"\[(\d{1,2})\]")
_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


@dataclass(frozen=True, slots=True)
class CheckedAnswer:
    paragraphs: list[str]
    followups: list[str]
    used: list[int]  # 1-based indexes of the sources actually cited, in order of first use
    dropped_paragraphs: int = 0
    removed_citations: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def is_grounded(self) -> bool:
        return bool(self.paragraphs) and bool(self.used)


def parse_reply(text: str) -> tuple[list[str], list[str]]:
    """``(paragraphs, followups)`` from the model's JSON, tolerating prose around it."""
    match = _JSON_BLOCK.search(text)
    if match:
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            paragraphs = [str(p).strip() for p in data.get("paragraphs") or [] if str(p).strip()]
            followups = [str(f).strip() for f in data.get("followups") or [] if str(f).strip()]
            return paragraphs, followups[:3]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return paragraphs, []


def _clean_paragraph(paragraph: str, source_count: int) -> tuple[str, list[int], int]:
    """``(text without dangling citations, valid indexes in order, removed count)``."""
    valid: list[int] = []
    removed = 0
    pieces: list[str] = []
    last = 0
    for match in _CITATION.finditer(paragraph):
        pieces.append(paragraph[last : match.start()])
        index = int(match.group(1))
        if 1 <= index <= source_count:
            valid.append(index)
            pieces.append(match.group(0))
        else:
            removed += 1
        last = match.end()
    pieces.append(paragraph[last:])
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", "".join(pieces)).strip()
    return cleaned, valid, removed


def check(text: str, source_count: int) -> CheckedAnswer:
    """Keep only paragraphs whose citations resolve; renumber nothing."""
    paragraphs, followups = parse_reply(text)
    kept: list[str] = []
    used: list[int] = []
    removed = 0
    dropped = 0
    for paragraph in paragraphs:
        cleaned, valid, gone = _clean_paragraph(paragraph, source_count)
        removed += gone
        if not valid:
            dropped += 1
            continue
        kept.append(cleaned)
        for index in valid:
            if index not in used:
                used.append(index)
    warnings: list[str] = []
    if dropped:
        warnings.append(f"{dropped} paragraph(s) had no valid citation and were dropped")
    if removed:
        warnings.append(f"{removed} citation(s) pointed at no source and were removed")
    if paragraphs and not kept:
        # Nothing cited: keep the first paragraph as a plain statement so the
        # user still sees what the model said (typically "the sources do not cover this").
        kept = [_CITATION.sub("", paragraphs[0]).strip()]
    return CheckedAnswer(kept, followups, used, dropped, removed, warnings)
