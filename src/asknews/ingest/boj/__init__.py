"""Bank of Japan publications.

The Bank publishes in Japanese: regular press conferences as HTML pages
since 2018 and as PDFs before that, and statements, minutes, outlook
reports and speeches as PDFs. This package holds the Japanese text helpers
the source modules share.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from datetime import date

BASE_URL = "https://www.boj.or.jp"
INSTITUTION = "boj"

# Japanese text is denser than English; keep segments shorter in characters.
SEGMENT_CHARS = 600

_CJK = "　-ヿ㐀-鿿＀-￯"
_SPACED_CJK = re.compile(f"(?<=[{_CJK}])[ 　]+(?=[{_CJK}])")
_SENTENCE_END = re.compile("(?<=[。！？])")
_JA_DATE = re.compile(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")
_JA_MONTH_DAY = re.compile(r"(\d{1,2})月\s*(\d{1,2})日")
_DOTTED_DATE = re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})")
_SIZE_SUFFIX = re.compile(r"\s*\[PDF[^\]]*\]\s*$")
_NBSP = " "


def normalise(text: str) -> str:
    """NFKC-normalise (full-width digits and Latin become ASCII) and remove the
    letter-spacing some PDF layouts insert between Japanese characters."""
    text = unicodedata.normalize("NFKC", text).replace(_NBSP, " ")
    return _SPACED_CJK.sub("", text)


def clean_title(text: str) -> str:
    """Index-page link text without the trailing ``[PDF 123KB]`` size note."""
    return _SIZE_SUFFIX.sub("", normalise(text)).strip()


def parse_ja_date(text: str) -> date | None:
    """First ``YYYY年M月D日`` or ``YYYY.M.D`` in ``text``."""
    match = _JA_DATE.search(normalise(text)) or _DOTTED_DATE.search(normalise(text))
    if match is None:
        return None
    year, month, day = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_month_day(text: str, *, posted: date) -> date | None:
    """``M月D日`` resolved against the posting date (an event posted in January
    with a December date belongs to the previous year)."""
    match = _JA_MONTH_DAY.search(normalise(text))
    if match is None:
        return None
    month, day = int(match.group(1)), int(match.group(2))
    year = posted.year - 1 if month > posted.month else posted.year
    try:
        return date(year, month, day)
    except ValueError:
        return None


def split_sentences(text: str) -> list[str]:
    return [s for s in (part.strip() for part in _SENTENCE_END.split(text)) if s]


def pack_ja(paragraphs: Sequence[str], *, max_chars: int = SEGMENT_CHARS) -> list[str]:
    """Pack paragraphs into chunks of at most ``max_chars`` characters, splitting
    an over-long paragraph at sentence ends."""
    chunks: list[str] = []
    buffer: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal buffer, size
        if buffer:
            chunks.append("\n".join(buffer))
        buffer, size = [], 0

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            flush()
            piece = ""
            for sentence in split_sentences(paragraph):
                if piece and len(piece) + len(sentence) > max_chars:
                    chunks.append(piece)
                    piece = ""
                piece += sentence
            if piece:
                chunks.append(piece)
            continue
        if buffer and size + len(paragraph) + 1 > max_chars:
            flush()
        buffer.append(paragraph)
        size += len(paragraph) + 1
    flush()
    return chunks
