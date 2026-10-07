from __future__ import annotations

import sys
import types
from typing import Any

from asknews.embed import local


class _FakeModel:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.calls: list[tuple[str, object]] = []

    def encode(self, texts: object, **_options: object) -> Any:
        self.calls.append(("encode", texts))
        rows = texts if isinstance(texts, list) else [texts]
        vectors = [[1.0] + [0.0] * 767 for _ in rows]
        return vectors if isinstance(texts, list) else vectors[0]

    def encode_document(self, texts: list[str], **_options: object) -> list[list[float]]:
        self.calls.append(("encode_document", texts))
        return [[1.0] + [0.0] * 767 for _ in texts]

    def encode_query(self, text: str, **_options: object) -> list[float]:
        self.calls.append(("encode_query", text))
        return [1.0] + [0.0] * 767


def _install_fake(monkeypatch: object) -> None:
    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = _FakeModel  # type: ignore[attr-defined]
    sys.modules["sentence_transformers"] = module


def test_e5_gets_its_prefixes_and_gemma_its_prompts(monkeypatch: object) -> None:
    _install_fake(monkeypatch)
    try:
        e5 = local.LocalEmbedder("intfloat/multilingual-e5-base")
        e5.embed_documents(["a", "b"])
        e5.embed_query("q")
        assert e5._model.calls == [("encode", ["passage: a", "passage: b"]), ("encode", "query: q")]
        gemma = local.LocalEmbedder()
        gemma.embed_documents(["a"])
        gemma.embed_query("q")
        assert gemma.model_name == local.DEFAULT_MODEL_ID
        assert gemma._model.calls == [("encode_document", ["a"]), ("encode_query", "q")]
    finally:
        sys.modules.pop("sentence_transformers", None)
