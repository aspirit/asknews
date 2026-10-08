from __future__ import annotations

import pytest

from asknews.embed import select
from asknews.embed.base import HashEmbedder
from asknews.embed.client import RemoteEmbedder
from asknews.settings import Settings, get_settings


def _settings(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    monkeypatch.setattr(select, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]


def test_embedder_follows_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, ASKNEWS_EMBEDDER="hash")
    assert isinstance(select.embedder_from_settings(), HashEmbedder)
    _settings(monkeypatch, ASKNEWS_EMBEDDER="remote", ASKNEWS_EMBED_URL="http://embed.test")
    assert isinstance(select.embedder_from_settings(), RemoteEmbedder)
    _settings(monkeypatch, ASKNEWS_EMBEDDER="nonsense")
    with pytest.raises(ValueError, match="unknown embedder"):
        select.embedder_from_settings()
    get_settings.cache_clear()
