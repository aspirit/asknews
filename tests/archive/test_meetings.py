from datetime import date

from asknews.archive.meetings import (
    boj_decision,
    expected_on,
    fed_decision,
    meeting_date_from_text,
    part_status,
)

EN_DASH = chr(0x2013)


def test_meeting_date_from_japanese_text() -> None:
    assert meeting_date_from_text("7月30、31日開催分", date(2026, 9, 28)) == date(2026, 7, 31)
    assert meeting_date_from_text("12月18、19日開催分", date(2026, 1, 27)) == date(2025, 12, 19)
    assert meeting_date_from_text("4月30日、5月1日開催分", date(2025, 6, 20)) is not None
    assert meeting_date_from_text("no dates", date(2026, 1, 1)) is None


def test_fed_decision_reads_range_change_and_vote() -> None:
    cut = (
        "the Committee decided to lower the target range for the federal funds rate by 1/4 "
        "percentage point to 4 to 4-1/4 percent. Voting for the monetary policy action were "
        "Jerome H. Powell, Chair; John C. Williams, Vice Chair; Michael S. Barr; Michelle W. "
        "Bowman; Susan M. Collins; Lisa D. Cook; Austan D. Goolsbee; Philip N. Jefferson; "
        "Alberto G. Musalem; Jeffrey R. Schmid; and Christopher J. Waller. Voting against "
        "this action was Stephen I. Miran, who preferred to lower the target range by 1/2 "
        "percentage point at this meeting."
    )
    decision = fed_decision(cut)
    assert decision is not None
    assert (decision.text, decision.low, decision.high) == ("4 to 4-1/4 percent", 4.0, 4.25)
    assert (decision.change_bp, decision.action) == (-25, "lower")
    assert decision.range_text == "4.00" + EN_DASH + "4.25%"
    assert decision.dissenters == ("Stephen I. Miran",)
    assert (decision.unanimous, decision.votes) == (False, "11-1")

    hold = (
        "the Committee decided to maintain the target range for the federal funds rate "
        "at 5-1/4 to 5-1/2 percent. Voting for the monetary policy action were Ann B. Cee and "
        "Dan E. Eff."
    )
    held = fed_decision(hold)
    assert held is not None
    assert (held.change_bp, held.action, held.unanimous, held.votes) == (0, "hold", True, "2-0")
    assert fed_decision(None) is None


def test_fed_dissenters_with_two_names() -> None:
    text = (
        "decided to raise the target range for the federal funds rate by 1/4 percentage point "
        "to 3-3/4 to 4 percent. Voting against the monetary policy action were Beth M. Hammack, "
        "who preferred no change, and Jeffrey R. Schmid, who preferred to raise by 1/2 point."
    )
    decision = fed_decision(text)
    assert decision is not None
    assert decision.change_bp == 25
    assert decision.range_text == "3.75" + EN_DASH + "4.00%"
    assert decision.dissenters == ("Beth M. Hammack", "Jeffrey R. Schmid")


def test_boj_decision_rate_change_and_vote() -> None:
    body = (
        "次回金融政策決定会合までの金融市場調節方針を、以下のとおりとすることを決定した"
        "（賛成7反対2）（注）。無担保コールレート（オーバーナイト物）を、1.25%程度で推移するよう"
        "促す。（注）賛成：植田委員、氷見野委員。反対：浅田委員、佐藤委員。"
    )
    decision = boj_decision(body, previous_rate=1.0)
    assert decision is not None
    assert (decision.text, decision.low, decision.change_bp) == ("1.25%", 1.25, 25)
    assert decision.range_text == "1.25%"
    assert (decision.votes, decision.unanimous) == ("7-2", False)
    assert decision.dissenters == ("浅田", "佐藤")
    first = boj_decision("無担保コールレートを、0.5%程度で推移するよう促す。全員一致。")
    assert first is not None
    assert (first.change_bp, first.action, first.unanimous) == (None, "set", True)
    negative = boj_decision("政策金利残高に" + chr(0x25B2) + "0.1%のマイナス金利を適用する。")
    assert negative is not None
    assert negative.low == -0.1
    assert boj_decision("no rate here") is None


def test_expected_documents_follow_each_publisher_schedule() -> None:
    fed_jan_2026 = date(2026, 1, 28)
    assert expected_on("fed", "press_conference", fed_jan_2026) == fed_jan_2026
    assert expected_on("fed", "outlook", fed_jan_2026) is None  # projections are quarterly
    assert expected_on("fed", "outlook", date(2026, 9, 16)) == date(2026, 9, 16)
    assert expected_on("fed", "minutes", date(2026, 9, 16)) == date(2026, 10, 7)
    assert expected_on("fed", "press_conference", date(2015, 1, 28)) is None
    assert expected_on("fed", "press_conference", date(2012, 1, 25)) == date(2012, 1, 25)
    assert expected_on("boj", "opinions", date(2015, 12, 18)) is None
    assert expected_on("boj", "opinions", date(2026, 9, 18)) == date(2026, 9, 28)
    assert expected_on("boj", "outlook", date(2026, 7, 31)) == date(2026, 7, 31)
    assert expected_on("boj", "outlook", date(2026, 9, 18)) is None


def test_part_status_distinguishes_scheduled_missing_and_none() -> None:
    today = date(2026, 10, 7)
    when = date(2026, 9, 16)
    assert part_status("fed", "minutes", when, None, today=today) == "scheduled"
    assert part_status("fed", "minutes", when, None, today=date(2026, 10, 8)) == "missing"
    assert part_status("fed", "outlook", date(2026, 1, 28), None, today=today) == "none"
    jan = date(2026, 1, 28)
    assert part_status("fed", "press_conference", jan, None, today=today) == "missing"
