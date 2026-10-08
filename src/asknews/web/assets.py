"""Versioned URLs for the site's scripts and styles.

Static files are cached for days at the edge and in browsers, so a changed
script would keep running in its old form after a deploy. Every reference
carries a hash of the file's content: a change is a new URL, and an unchanged
file stays cached.
"""

from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"


@cache
def version(name: str) -> str:
    """The first eight hex digits of the file's SHA-256, computed once."""
    return hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()[:8]


def asset(name: str) -> str:
    """``/static/<name>?v=<hash>`` for a file under the static directory."""
    return f"/static/{name}?v={version(name)}"
