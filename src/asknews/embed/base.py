"""The embedder interface and a deterministic stand-in."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Protocol

DIMS = 768  # fixed by the schema (segment_embeddings.embedding is vector(768))


class Embedder(Protocol):
    """Anything that turns text into unit vectors of :data:`DIMS` dimensions."""

    @property
    def model_name(self) -> str: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


# Words for Latin scripts; single characters for kana and kanji (no tokenizer needed).
_KANA_KANJI = f"{chr(0x3040)}-{chr(0x30FF)}{chr(0x3400)}-{chr(0x9FFF)}"
_TOKEN = re.compile(f"[A-Za-z0-9]+|[{_KANA_KANJI}]")


class HashEmbedder:
    """Bag-of-tokens hashed into a unit vector.

    Not a semantic model: two texts are close when they share words (or, for
    Japanese, characters). That is enough to exercise every code path that
    stores, indexes and ranks vectors without downloading weights.
    """

    model_name = "hash-v1"

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * DIMS
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % DIMS
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def to_pgvector(vector: Sequence[float]) -> str:
    """Literal accepted by pgvector's ``vector`` input function."""
    return "[" + ",".join(f"{v:.7g}" for v in vector) + "]"
