"""Repository-wide conventions that are cheaper to test than to review.

The project is bilingual at the product level but monolingual at the code
level: source, comments, documentation and commit messages are English, and
every user-facing Japanese or other non-ASCII string lives in a locale file,
a domain configuration file, a test fixture, or a parser for a Japanese
source (and that parser's tests). Keeping that boundary sharp
means a reviewer never has to guess which strings reach users.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Directories whose files may contain non-ASCII text. Parsers for Japanese
# publications legitimately carry the Japanese markers they look for.
LOCALIZED_PREFIXES = (
    "src/asknews/locales/",
    "src/asknews/ingest/boj/",
    "tests/ingest/boj/",
    "domain/",
    "tests/fixtures/",
)

# Tracked files that are not text and are skipped by the encoding check.
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".pdf"}


def tracked_files() -> list[Path]:
    """Every file git tracks or would track: new files count before they are committed."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    names = {name for name in result.stdout.decode().split("\0") if name}
    return [Path(name) for name in sorted(names) if (REPO_ROOT / name).is_file()]


def is_localized(path: Path) -> bool:
    return path.as_posix().startswith(LOCALIZED_PREFIXES)


def non_ascii_lines(path: Path) -> list[int]:
    text = (REPO_ROOT / path).read_bytes()
    return [number for number, line in enumerate(text.splitlines(), start=1) if not line.isascii()]


def test_source_is_ascii_outside_localized_paths() -> None:
    offenders: dict[str, list[int]] = {}
    for path in tracked_files():
        if is_localized(path) or path.suffix in BINARY_SUFFIXES:
            continue
        if lines := non_ascii_lines(path):
            offenders[path.as_posix()] = lines[:5]
    assert not offenders, f"non-ASCII text outside localized paths: {offenders}"


def test_localized_prefixes_are_directories() -> None:
    # Guard against a typo silently exempting nothing.
    assert all(prefix.endswith("/") for prefix in LOCALIZED_PREFIXES)
