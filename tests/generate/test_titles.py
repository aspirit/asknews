from __future__ import annotations

from datetime import date
from pathlib import Path

from asknews.domain_config import load_speakers
from asknews.generate.titles import english_title, japanese_title

REPO_ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = load_speakers(REPO_ROOT / "domain" / "speakers.yaml")
EN_DASH = chr(0x2013)


def test_bank_of_japan_titles_in_english() -> None:
    posted = date(2026, 9, 24)
    assert english_title("総裁定例記者会見（2026年9月18日）", posted, DIRECTORY) == (
        "Governor's regular press conference (September 18, 2026)"
    )
    assert english_title("総裁記者会見（2026年9月1日）", posted, DIRECTORY) == (
        "Governor's press conference (September 1, 2026)"
    )
    assert english_title("増審議委員記者会見（2026年9月10日）", posted, DIRECTORY) == (
        "Press conference by Board Member Masu (September 10, 2026)"
    )
    assert english_title("氷見野副総裁記者会見（2026年3月2日）", posted, DIRECTORY) == (
        "Press conference by Deputy Governor Himino (March 2, 2026)"
    )
    assert english_title("浅田審議委員就任記者会見（2026年4月1日）", posted, DIRECTORY) == (
        "Inaugural press conference by Board Member Asada (April 1, 2026)"
    )
    assert english_title(
        "総裁定例記者会見（内田副総裁代理・2026年6月16日）", posted, DIRECTORY
    ) == ("Press conference by Deputy Governor Uchida, for the Governor (June 16, 2026)")
    assert english_title(
        "当面の金融政策運営について（現状維持、12時14分公表）", posted, DIRECTORY
    ) == ("Statement on Monetary Policy")
    assert english_title("金融市場調節方針の変更について", posted, DIRECTORY) == (
        "Change in the Guideline for Money Market Operations"
    )
    assert english_title(
        "金融政策決定会合議事要旨（7月30、31日開催分）", date(2026, 9, 28), DIRECTORY
    ) == (f"Minutes of the Monetary Policy Meeting (July 30{EN_DASH}31, 2026)")
    assert english_title(
        "金融政策決定会合における主な意見（12月18、19日開催分）", date(2026, 1, 7), DIRECTORY
    ) == (f"Summary of Opinions at the Monetary Policy Meeting (December 18{EN_DASH}19, 2025)")
    assert english_title("経済・物価情勢の展望（2026年7月）基本的見解", posted, DIRECTORY) == (
        "Outlook for Economic Activity and Prices (July 2026): The Bank's View"
    )
    assert (
        english_title("【挨拶】（全国証券大会）", posted, DIRECTORY) is None
    )  # speeches are free text


def test_federal_reserve_titles_in_japanese() -> None:
    when = date(2026, 9, 16)
    assert japanese_title("FOMC Press Conference September 16, 2026", when, "press_conference") == (
        "FOMC記者会見（2026年9月16日）"
    )
    assert japanese_title("Federal Reserve issues FOMC statement", when, "statement") == (
        "FOMC声明（2026年9月16日）"
    )
    assert (
        japanese_title(
            f"Minutes of the Federal Open Market Committee, July 28{EN_DASH}29, 2026",
            date(2026, 8, 19),
            "minutes",
        )
        == f"FOMC議事要旨（2026年7月28{EN_DASH}29日）"
    )
    assert japanese_title(
        "Summary of Economic Projections, September 16, 2026", when, "report"
    ) == ("経済見通し（SEP・2026年9月16日）")
    assert japanese_title("The Inflation Outlook", when, "speech") is None
