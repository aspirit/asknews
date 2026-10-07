"""What a question asks for, read from its wording.

Nothing here understands the question; it only extracts signals that the
retrieval paths and the ranking can use: language, phrases to match exactly,
content terms, a publisher or speaker that was named, and a time window.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from asknews.domain_config import SpeakerDirectory
from asknews.retrieval.gate import Vocabulary

_KANA_KANJI = f"{chr(0x3040)}-{chr(0x30FF)}{chr(0x3400)}-{chr(0x9FFF)}"
_JA_CHAR = re.compile(f"[{_KANA_KANJI}]")
_KANJI_RUN = re.compile(f"[{chr(0x3400)}-{chr(0x9FFF)}]{{2,}}")
_KATAKANA_RUN = re.compile(f"[{chr(0x30A0)}-{chr(0x30FF)}]{{2,}}")
_ASCII_TERM = re.compile(r"[A-Za-z][A-Za-z0-9.%-]{1,}")
_QUOTE_OPEN = '"' + chr(0x201C) + chr(0x300C) + chr(0x300E)  # " and the curly/corner openers
_QUOTE_CLOSE = '"' + chr(0x201D) + chr(0x300D) + chr(0x300F)
_QUOTED = re.compile(f"[{_QUOTE_OPEN}]([^{_QUOTE_CLOSE}]{{2,80}})[{_QUOTE_CLOSE}]")
_EN_WORD = re.compile(r"[a-z][a-z'-]{2,}")
_STOPWORDS = (
    "the and for that with this what when where which who how why did does was were are "
    "about from into over under than then them they their there these those have has had "
    "will would could should can may might must shall been being also just like most some "
    "such very much many more less least last next first latest recent recently say said "
    "says tell told think thought mean meant explain explained describe compare between after "
    "before during since until while because into onto per via"
)
_EN_STOP = frozenset(_STOPWORDS.split())
_YEAR_MONTH_EN = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december)"
    r"\s+(20\d\d)\b",
    re.I,
)
_YEAR_EN = re.compile(r"\b(20\d\d)\b")
_NEN, _GATSU = chr(0x5E74), chr(0x6708)  # the year and month characters
_YEAR_MONTH_JA = re.compile(r"(20\d\d)" + _NEN + r"\s*(\d{1,2})" + _GATSU)
_YEAR_JA = re.compile(r"(20\d\d)" + _NEN)
_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ]
    )
}


@dataclass(frozen=True, slots=True)
class DateWindow:
    start: date
    end: date
    hard: bool  # a named period filters; a vague "recently" only boosts


@dataclass(frozen=True, slots=True)
class Query:
    text: str
    lang: str
    phrases: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()
    institutions: tuple[str, ...] = ()
    speakers: tuple[str, ...] = ()  # speaker slugs
    window: DateWindow | None = None
    recency: bool = False
    meta: dict[str, object] = field(default_factory=dict)


def detect_language(text: str) -> str:
    return "ja" if _JA_CHAR.search(text) else "en"


def _terms(text: str, lang: str) -> tuple[str, ...]:
    if lang == "ja":
        found = _KANJI_RUN.findall(text) + _KATAKANA_RUN.findall(text) + _ASCII_TERM.findall(text)
    else:
        found = [w for w in _EN_WORD.findall(text.lower()) if w not in _EN_STOP]
    seen: dict[str, None] = {}
    for term in found:
        seen.setdefault(term, None)
    return tuple(seen)


def _institutions(lowered: str, vocab: Vocabulary) -> tuple[str, ...]:
    padded = f" {lowered} "
    return tuple(
        code for code, cues in vocab.institutions.items() if any(cue in padded for cue in cues)
    )


def _window(text: str, lang: str, today: date, vocab: Vocabulary) -> tuple[DateWindow | None, bool]:
    ym = _YEAR_MONTH_JA.search(text) if lang == "ja" else _YEAR_MONTH_EN.search(text)
    if ym:
        if lang == "ja":
            year, month = int(ym.group(1)), int(ym.group(2))
        else:
            year, month = int(ym.group(2)), _MONTHS[ym.group(1).lower()]
        start = date(year, month, 1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return DateWindow(start, end, hard=True), False
    year_match = _YEAR_JA.search(text) if lang == "ja" else _YEAR_EN.search(text)
    if year_match:
        year = int(year_match.group(1))
        return DateWindow(date(year, 1, 1), date(year, 12, 31), hard=True), False
    words = vocab.recency.get(lang, ())
    lowered = text.lower()
    if any(word in lowered for word in words):
        return DateWindow(today - timedelta(days=90), today, hard=False), True
    return None, False


def analyse(
    text: str,
    directory: SpeakerDirectory | None = None,
    *,
    vocab: Vocabulary,
    today: date | None = None,
) -> Query:
    today = today or datetime.now(UTC).date()
    lang = detect_language(text)
    lowered = text.lower()
    phrases = tuple(p.strip() for p in _QUOTED.findall(text) if p.strip())
    window, recency = _window(text, lang, today, vocab)
    speakers: tuple[str, ...] = ()
    if directory is not None:
        hits = []
        for spec in directory.speakers:
            tokens = [t for t in spec.match if len(t) >= 2]
            if any((t.lower() in lowered) if t.isascii() else (t in text) for t in tokens):
                hits.append(spec.slug)
        speakers = tuple(dict.fromkeys(hits))
    return Query(
        text=text.strip(),
        lang=lang,
        phrases=phrases,
        terms=_terms(text, lang),
        institutions=_institutions(lowered, vocab),
        speakers=speakers,
        window=window,
        recency=recency,
    )
