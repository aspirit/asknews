"""Dense vectors for segments and queries.

The archive stores one vector per segment. Which model produces them is a
deployment choice: the real encoder runs as a small service, and a
deterministic hashing encoder stands in for it in tests and local
development so nothing here needs model weights.
"""

from __future__ import annotations

from asknews.embed.base import Embedder, HashEmbedder

__all__ = ["Embedder", "HashEmbedder"]
