"""Bank of Japan publications.

The Bank publishes in Japanese: regular press conferences as HTML pages
since 2018 and as PDFs before that, and statements, minutes, outlook
reports and speeches as PDFs. This package holds the Japanese text helpers
the source modules share.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date

BASE_URL = "https://www.boj.or.jp"
INSTITUTION = "boj"

# Japanese text is denser than English; keep segments shorter in characters.
SEGMENT_CHARS = 600

_CJK = "　-ヿ㐀-鿿＀-￯"
_SPACED_CJK = re.compile(f"(?<=[{_CJK}])[ 　]+(?=[{_CJK}])")
_SENTENCE_END = re.compile("(?<=[。！？])")
_JA_DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_JA_MONTH_DAY = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_DOTTED_DATE = re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})")
_SIZE_SUFFIX = re.compile(r"\s*\[PDF[^\]]*\]\s*$")
_NBSP = " "


# Full-width digits, Latin letters and the percent sign fold to ASCII so that
# "1.25%" is searchable however it was typeset; Japanese punctuation is kept.
_FOLD = {
    code: chr(code - 0xFEE0)
    for code in [*range(0xFF10, 0xFF1A), *range(0xFF21, 0xFF3B), *range(0xFF41, 0xFF5B), 0xFF05]
}
_FOLD[0x3000] = " "
_FOLD[0x00A0] = " "
# A full-width point or comma between digits is a decimal or thousands
# separator; PDF layouts sometimes space the point out ("0． 75").
_DOTTED_NUMBER = re.compile(r"(?<=\d)[．，](?=\d)|(?<=\d)． (?=\d)")
# A line holding nothing but a page number, as PDF text layers carry them.
_PAGE_NUMBER_LINE = re.compile(r"(?m)^[ \t]*\d{1,3}[ \t]*$\n?")
# PDF layouts also scatter single spaces inside dates ("202 6 年 7 月 3 1 日").
# Digits are joined only when a date word follows: a row of figures in a
# forecast table ("2.4 2.7 1.9") must keep its spaces.
_SPACED_NUMBER = re.compile(r"(?<=\d) (?=\d+ ?[年月日%])|(?<=\d) (?=[年月日%])|(?<=[年月]) (?=\d)")


def normalise(text: str) -> str:
    """Fold full-width digits, letters and percent to ASCII, fold decimal
    separators inside numbers, and remove the letter-spacing some PDF layouts
    insert between Japanese characters."""
    text = text.translate(_FOLD)
    text = _DOTTED_NUMBER.sub(lambda m: "." if "．" in m.group(0) else ",", text)
    text = _SPACED_NUMBER.sub("", text)
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


def strip_page_numbers(text: str) -> str:
    """Remove lines that hold only a page number, before line wraps are joined."""
    return _PAGE_NUMBER_LINE.sub("", text)


def pdf_paragraphs(text: str) -> list[str]:
    """Paragraphs of a Japanese PDF text layer: blank lines separate paragraphs
    and line wraps inside a paragraph carry no meaning, so they are removed.
    Page numbers on their own lines are dropped first."""
    out: list[str] = []
    for block in re.split(r"\n\s*\n", strip_page_numbers(normalise(text))):
        joined = re.sub(r"[ \t]*\n[ \t]*", "", block).strip()
        if joined:
            out.append(joined)
    return out
