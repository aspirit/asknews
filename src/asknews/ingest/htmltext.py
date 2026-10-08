"""Turning article HTML into clean paragraphs.

Publisher pages wrap the article in navigation, banners and footers. The
caller points at the article with start and end markers; this module turns
that window into a list of paragraphs and packs them into segments.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Sequence
from html.parser import HTMLParser

from asknews.ingest.turns import window_text

_BLOCK_TAGS = frozenset(
    {
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "blockquote",
        "td",
        "th",
        "div",
        "section",
        "article",
        "tr",
        "br",
    }
)
_SKIP_TAGS = frozenset({"script", "style", "noscript", "nav", "header", "footer", "svg"})
_SPACE = re.compile(r"\s+")


class _ParagraphParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.paragraphs: list[str] = []
        self._buffer: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.flush()

    def handle_startendtag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self.flush()

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._buffer.append(data)

    def handle_entityref(self, name: str) -> None:
        self.handle_data(html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        self.handle_data(html.unescape(f"&#{name};"))

    def flush(self) -> None:
        text = _SPACE.sub(" ", "".join(self._buffer)).strip()
        self._buffer = []
        if text:
            self.paragraphs.append(text)


class _TableParser(HTMLParser):
    """Cells of the first table in a fragment, row by row."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._depth = 0
        self._done = False

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if self._done:
            return
        if tag == "table":
            self._depth += 1
        elif tag == "tr" and self._depth:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if self._done:
            return
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(_SPACE.sub(" ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif tag == "table" and self._depth:
            self._depth -= 1
            self._done = self._depth == 0

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def table_rows(markup: str) -> list[list[str]]:
    """The cells of the first table in ``markup``, one list per row."""
    parser = _TableParser()
    parser.feed(markup)
    return parser.rows


def paragraphs(markup: str) -> list[str]:
    """Block-level text runs of ``markup``, whitespace-normalised, in order."""
    parser = _ParagraphParser()
    parser.feed(markup)
    parser.flush()
    return parser.paragraphs


def content_window(markup: str, *, start: Sequence[str], end: Sequence[str]) -> str:
    """The slice of ``markup`` from the first start marker to the first end marker after it.

    Markers are literal substrings (an element id, a class attribute); the
    window is widened to the opening ``<`` of the element that carries each
    marker, so the start element is included and the end element excluded.
    A missing start marker means the window starts at the beginning; a
    missing end marker means it runs to the end.
    """
    begin = min((i for i in (markup.find(m) for m in start) if i >= 0), default=0)
    begin = max(markup.rfind("<", 0, begin + 1), 0)
    stop = min((i for i in (markup.find(m, begin + 1) for m in end) if i >= 0), default=len(markup))
    if stop < len(markup):
        stop = max(markup.rfind("<", begin, stop + 1), begin)
    return markup[begin:stop]


def drop_boilerplate(items: Iterable[str], *, drop: Iterable[str]) -> list[str]:
    """Remove paragraphs that equal one of the boilerplate strings, or start
    with one that is a phrase. A single word ("Share") only matches a
    paragraph that is that word, not every paragraph beginning with it."""
    patterns = tuple(drop)
    return [p for p in items if not any(p == d or (" " in d and p.startswith(d)) for d in patterns)]


def pack_paragraphs(items: Sequence[str], *, max_chars: int = 900) -> list[str]:
    """Pack consecutive paragraphs into chunks of at most ``max_chars``.

    Paragraph boundaries are kept (joined by blank lines); a paragraph longer
    than the limit is split at sentence boundaries on its own.
    """
    chunks: list[str] = []
    buffer: list[str] = []
    size = 0
    for paragraph in items:
        if len(paragraph) > max_chars:
            if buffer:
                chunks.append("\n\n".join(buffer))
                buffer, size = [], 0
            chunks.extend(window_text(paragraph, max_chars=max_chars))
            continue
        if buffer and size + len(paragraph) + 2 > max_chars:
            chunks.append("\n\n".join(buffer))
            buffer, size = [], 0
        buffer.append(paragraph)
        size += len(paragraph) + 2
    if buffer:
        chunks.append("\n\n".join(buffer))
    return chunks
