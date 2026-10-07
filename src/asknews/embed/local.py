"""Embedding models run in-process through sentence-transformers.

Only imported when the ``model`` extra is installed. Two models are known:

* ``google/embeddinggemma-300m`` (the default): 768 dimensions, with the
  query and document prompts the model ships with. The weights are gated on
  Hugging Face, so the first run needs an authenticated client.
* ``intfloat/multilingual-e5-base``: 768 dimensions, open weights, and the
  ``query:`` / ``passage:`` prefixes its card prescribes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from asknews.embed.base import DIMS

DEFAULT_MODEL_ID = "google/embeddinggemma-300m"
# Models that expect a literal prefix instead of carrying prompts of their own.
PREFIXES: dict[str, tuple[str, str]] = {
    "intfloat/multilingual-e5-base": ("query: ", "passage: "),
    "intfloat/multilingual-e5-small": ("query: ", "passage: "),
}


class LocalEmbedder:
    def __init__(self, model_id: str = DEFAULT_MODEL_ID, device: str | None = None) -> None:
        from sentence_transformers import SentenceTransformer

        self.model_name = model_id
        self._prefixes = PREFIXES.get(model_id)
        self._model: Any = SentenceTransformer(model_id, device=device, truncate_dim=DIMS)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if self._prefixes:
            prefix = self._prefixes[1]
            vectors = self._model.encode([prefix + t for t in texts], normalize_embeddings=True)
        else:
            vectors = self._model.encode_document(list(texts), normalize_embeddings=True)
        return [[float(x) for x in row] for row in vectors]

    def embed_query(self, text: str) -> list[float]:
        if self._prefixes:
            vector = self._model.encode(self._prefixes[0] + text, normalize_embeddings=True)
        else:
            vector = self._model.encode_query(text, normalize_embeddings=True)
        return [float(x) for x in vector]
