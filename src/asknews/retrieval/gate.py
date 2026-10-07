"""Deciding whether a question is about the archive at all."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from asknews.domain_config import SpeakerDirectory
from asknews.settings import get_settings

if TYPE_CHECKING:
    from asknews.retrieval.query import Query


@dataclass(frozen=True, slots=True)
class Vocabulary:
    en: tuple[str, ...]
    ja: tuple[str, ...]
    institutions: dict[str, tuple[str, ...]] = field(default_factory=dict)
    recency: dict[str, tuple[str, ...]] = field(default_factory=dict)


def load_vocabulary(path: Path) -> Vocabulary:
    raw = yaml.safe_load(path.read_text("utf-8")) or {}
    terms = raw.get("terms") or {}
    return Vocabulary(
        en=tuple(str(t).lower() for t in terms.get("en") or ()),
        ja=tuple(str(t) for t in terms.get("ja") or ()),
        institutions={
            str(code): tuple(str(c).lower() for c in cues or ())
            for code, cues in (raw.get("institutions") or {}).items()
        },
        recency={
            str(lang): tuple(str(w).lower() for w in words or ())
            for lang, words in (raw.get("recency") or {}).items()
        },
    )


@lru_cache(maxsize=1)
def vocabulary() -> Vocabulary:
    return load_vocabulary(get_settings().domain_dir / "gate.yaml")


def mentions_domain(query: Query, vocab: Vocabulary, directory: SpeakerDirectory | None) -> bool:
    """True when the wording names the domain: a term, a publisher or a speaker."""
    if query.institutions or query.speakers:
        return True
    lowered = " " + re.sub(r"[^a-z0-9\s]", " ", query.text.lower()) + " "
    if any(f" {term} " in lowered or f" {term}s " in lowered for term in vocab.en):
        return True
    if any(term in query.text for term in vocab.ja):
        return True
    if directory is not None:
        for spec in directory.speakers:
            if any(
                (t.lower() in lowered) if t.isascii() else (t in query.text) for t in spec.match
            ):
                return True
    return False
