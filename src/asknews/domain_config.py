"""Domain configuration: the part of the product that names real people,
institutions and curated content.

Everything here is data loaded from the ``domain`` directory, so the code
stays free of names and the configuration can be edited without a release.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

from asknews.settings import get_settings


@dataclass(frozen=True, slots=True)
class SpeakerSpec:
    institution: str
    slug: str
    name: str
    role: str | None
    match: tuple[str, ...]
    name_ja: str | None = None
    tenure: tuple[date, date | None] | None = None  # when the person held the top job


@dataclass(frozen=True, slots=True)
class SpeakerDirectory:
    speakers: tuple[SpeakerSpec, ...] = field(default_factory=tuple)

    def find(self, institution: str, text: str, *, when: date | None = None) -> SpeakerSpec | None:
        """The speaker of ``institution`` whose match token occurs in ``text``.

        When several match (two board members sharing a surname) and a date is
        given, the one whose recorded tenure covers the date wins; otherwise
        the first entry does.
        """
        candidates = [
            spec
            for spec in self.speakers
            if spec.institution == institution and any(token in text for token in spec.match)
        ]
        if when is not None:
            for spec in candidates:
                if spec.tenure and spec.tenure[0] <= when <= (spec.tenure[1] or date.max):
                    return spec
            # Every candidate has a tenure and none covers the date: a namesake we
            # do not know yet, not the person on record.
            candidates = [spec for spec in candidates if spec.tenure is None]
        return candidates[0] if candidates else None

    def get(self, slug: str) -> SpeakerSpec | None:
        return next((s for s in self.speakers if s.slug == slug), None)

    def head_on(self, institution: str, when: date) -> SpeakerSpec | None:
        """The speaker whose tenure as head of ``institution`` covers ``when``."""
        for spec in self.speakers:
            if spec.institution != institution or spec.tenure is None:
                continue
            start, end = spec.tenure
            if start <= when and (end is None or when <= end):
                return spec
        return None


def _tenure(value: object) -> tuple[date, date | None] | None:
    if not isinstance(value, dict) or "from" not in value:
        return None
    start = date.fromisoformat(str(value["from"]))
    end = date.fromisoformat(str(value["to"])) if value.get("to") else None
    return (start, end)


def _speaker_spec(institution: str, entry: dict[str, object]) -> SpeakerSpec:
    match = entry.get("match") or []
    return SpeakerSpec(
        institution=institution,
        slug=str(entry["slug"]),
        name=str(entry["name"]),
        role=str(entry["role"]) if entry.get("role") else None,
        match=tuple(str(m) for m in match) if isinstance(match, list) else (),
        name_ja=str(entry["name_ja"]) if entry.get("name_ja") else None,
        tenure=_tenure(entry.get("tenure")),
    )


def load_speakers(path: Path) -> SpeakerDirectory:
    raw: dict[str, list[dict[str, object]] | None] = yaml.safe_load(path.read_text("utf-8")) or {}
    specs = [
        _speaker_spec(str(institution), entry)
        for institution, entries in raw.items()
        for entry in entries or []
    ]
    return SpeakerDirectory(tuple(specs))


@lru_cache(maxsize=1)
def speakers() -> SpeakerDirectory:
    return load_speakers(get_settings().domain_dir / "speakers.yaml")
