"""EmbeddingGemma through sentence-transformers.

Only imported when the ``model`` extra is installed; the weights are gated
on Hugging Face, so the first run needs an authenticated client.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from asknews.embed.base import DIMS

MODEL_ID = "google/embeddinggemma-300m"


class GemmaEmbedder:
    model_name = MODEL_ID

    def __init__(self, device: str | None = None) -> None:
        from sentence_transformers import SentenceTransformer

        self._model: Any = SentenceTransformer(MODEL_ID, device=device, truncate_dim=DIMS)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode_document(list(texts), normalize_embeddings=True)
        return [[float(x) for x in row] for row in vectors]

    def embed_query(self, text: str) -> list[float]:
        vector = self._model.encode_query(text, normalize_embeddings=True)
        return [float(x) for x in vector]
