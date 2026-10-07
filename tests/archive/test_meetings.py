from datetime import date

from asknews.archive.meetings import boj_decision, fed_decision, meeting_date_from_text


def test_meeting_date_from_japanese_text() -> None:
    assert meeting_date_from_text("7月30、31日開催分", date(2026, 9, 28)) == date(2026, 7, 31)
    assert meeting_date_from_text("12月18、19日開催分", date(2026, 1, 27)) == date(2025, 12, 19)
    assert meeting_date_from_text("4月30日、5月1日開催分", date(2025, 6, 20)) is not None
    assert meeting_date_from_text("no dates", date(2026, 1, 1)) is None


def test_decision_extraction() -> None:
    cut = (
        "the Committee decided to lower the target range for the federal funds rate by 1/4 "
        "percentage point to 4 to 4-1/4 percent."
    )
    assert fed_decision(cut) == "4 to 4-1/4 percent"
    hold = (
        "the Committee decided to maintain the target range for the federal funds rate "
        "at 5-1/4 to 5-1/2 percent."
    )
    assert fed_decision(hold) == "5-1/4 to 5-1/2 percent"
    assert fed_decision(None) is None
    assert boj_decision("無担保コールレートを、1.25%程度で推移するよう促す。") == "1.25%"
    assert boj_decision("no rate here") is None
