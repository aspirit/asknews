"""Finding the passages a question should be answered from.

Two independent paths look at every question: lexical search, which is what
finds rare terms, names and exact phrases, and dense retrieval, which finds
paraphrases and works across languages. Their results are fused, boosted by
what the question asked for (a publisher, a speaker, a period), and reduced
to a short list of citations.
"""

from __future__ import annotations

from asknews.retrieval.pipeline import Retrieval, retrieve

__all__ = ["Retrieval", "retrieve"]
