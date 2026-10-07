"""Interface strings in English and Japanese, read from the locale files."""

from __future__ import annotations

import datetime as dt
import json
from functools import cache
from pathlib import Path
from typing import Any

LOCALES_DIR = Path(__file__).resolve().parents[1] / "locales"
LANGUAGES = ("en", "ja")
DEFAULT_LANGUAGE = "en"
JST = dt.timezone(dt.timedelta(hours=9))


@cache
def strings(lang: str) -> dict[str, Any]:
    path = LOCALES_DIR / f"{lang}.json"
    data: dict[str, Any] = json.loads(path.read_text("utf-8"))
    return data


class Translator:
    """``t("nav.home")`` style lookup with English as the fallback."""

    def __init__(self, lang: str) -> None:
        self.lang = lang if lang in LANGUAGES else DEFAULT_LANGUAGE
        self._strings = strings(self.lang)
        self._fallback = strings(DEFAULT_LANGUAGE)

    def __call__(self, key: str, **params: object) -> str:
        value = self._strings.get(key, self._fallback.get(key, key))
        text = str(value)
        return text.format(**params) if params else text

    def get(self, key: str) -> Any:
        return self._strings.get(key, self._fallback.get(key))

    def date(self, when: dt.datetime, *, form: str = "full") -> str:
        local = when.astimezone(JST) if self.lang == "ja" else when
        months: list[str] = self.get("date.months")
        return self(f"date.{form}", y=local.year, mon=months[local.month - 1], d=local.day)

    def date_only(self, when: dt.date, *, form: str = "full") -> str:
        months: list[str] = self.get("date.months")
        return self(f"date.{form}", y=when.year, mon=months[when.month - 1], d=when.day)

    def kind(self, kind: str) -> str:
        return self(f"kind.{kind}")

    def institution(self, code: str, *, short: bool = False) -> str:
        key = f"inst.{code}.short" if short else f"inst.{code}"
        return self(key)


def pick_language(value: str | None) -> str:
    return value if value in LANGUAGES else DEFAULT_LANGUAGE


def duration(seconds: int | float | None) -> str:
    if seconds is None:
        return ""
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
