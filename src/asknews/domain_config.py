"""Domain configuration: the part of the product that names real people,
institutions and curated content.

Everything here is data loaded from the ``domain`` directory, so the code
stays free of names and the configuration can be edited without a release.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass(frozen=True, slots=True)
class SpeakerDirectory:
    speakers: tuple[SpeakerSpec, ...] = field(default_factory=tuple)

    def find(self, institution: str, text: str) -> SpeakerSpec | None:
        """The first speaker of ``institution`` whose match token occurs in ``text``."""
        for spec in self.speakers:
            if spec.institution == institution and any(token in text for token in spec.match):
                return spec
        return None

    def get(self, slug: str) -> SpeakerSpec | None:
        return next((s for s in self.speakers if s.slug == slug), None)


def _speaker_spec(institution: str, entry: dict[str, object]) -> SpeakerSpec:
    match = entry.get("match") or []
    return SpeakerSpec(
        institution=institution,
        slug=str(entry["slug"]),
        name=str(entry["name"]),
        role=str(entry["role"]) if entry.get("role") else None,
        match=tuple(str(m) for m in match) if isinstance(match, list) else (),
        name_ja=str(entry["name_ja"]) if entry.get("name_ja") else None,
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
