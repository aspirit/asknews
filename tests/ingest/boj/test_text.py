from datetime import date

from asknews.ingest.boj import (
    clean_title,
    normalise,
    pack_ja,
    parse_ja_date,
    parse_month_day,
    split_sentences,
)

FULLWIDTH_DATE = "２０２６年９月１８日"  # 2026年9月18日 in full-width digits
KUTEN = "。"  # the Japanese full stop


def test_normalise_folds_fullwidth_digits_and_letter_spacing() -> None:
    assert normalise(FULLWIDTH_DATE) == "2026" + "年" + "9" + "月" + "18" + "日"
    spaced = "本 議 事 要 旨"  # 本 議 事 要 旨
    assert normalise(spaced) == "本議事要旨"
    assert normalise("rate 1.25% stays") == "rate 1.25% stays"
    assert normalise("１．２５％") == "1.25%"
    assert normalise("（問）") == "（問）"  # Japanese punctuation is left alone
    assert normalise("202 6 年 7 月 3 1 日") == "2026年7月31日"


def test_dates() -> None:
    assert parse_ja_date("――" + FULLWIDTH_DATE + "（金）") == date(2026, 9, 18)
    assert parse_ja_date("2026.6.19") == date(2026, 6, 19)
    assert parse_ja_date("no date here") is None
    assert parse_month_day("（12月19日）", posted=date(2026, 1, 5)) == date(2025, 12, 19)
    assert parse_month_day("（9月18日）", posted=date(2026, 9, 24)) == date(2026, 9, 18)


def test_clean_title_strips_size_note() -> None:
    assert clean_title("総裁記者会見 [PDF 249KB]") == "総裁記者会見"


def test_sentence_split_and_packing() -> None:
    sentences = split_sentences("あ" + KUTEN + "いう" + KUTEN + "え")
    assert sentences == ["あ" + KUTEN, "いう" + KUTEN, "え"]
    long = ("あ" * 50 + KUTEN) * 10
    chunks = pack_ja([long, "short"], max_chars=120)
    assert all(len(c) <= 120 for c in chunks)
    assert chunks[-1] == "short"
    assert pack_ja(["a", "b"], max_chars=10) == ["a\nb"]
