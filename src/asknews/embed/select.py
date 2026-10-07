"""Choose the embedder from configuration."""

from __future__ import annotations

from asknews.embed.base import Embedder, HashEmbedder
from asknews.settings import get_settings


def embedder_from_settings() -> Embedder:
    settings = get_settings()
    if settings.embedder == "hash":
        return HashEmbedder()
    if settings.embedder == "remote":
        from asknews.embed.client import RemoteEmbedder

        return RemoteEmbedder(settings.embed_url)
    if settings.embedder == "local":
        from asknews.embed.local import LocalEmbedder

        return LocalEmbedder(settings.embed_model)
    raise ValueError(f"unknown embedder: {settings.embedder}")
