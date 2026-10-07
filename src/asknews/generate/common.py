"""Helpers shared by the generators."""

from __future__ import annotations

import json
import re
from typing import Any

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)
LANGUAGE_NAMES = {"en": "English", "ja": "Japanese"}


def parse_json_object(text: str) -> dict[str, Any]:
    """The first JSON object in a model reply, or an empty dict."""
    match = _JSON_BLOCK.search(text)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def clip(text: str | None, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " ..."


def other_language(lang: str) -> str:
    return "ja" if lang == "en" else "en"
