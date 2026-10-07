from datetime import UTC, datetime

from asknews.web.i18n import Translator


def test_dates_follow_the_publisher_not_the_reader() -> None:
    washington_afternoon = datetime(2026, 9, 16, 18, 30, tzinfo=UTC)
    tokyo_afternoon = datetime(2026, 9, 18, 6, 30, tzinfo=UTC)
    assert Translator("en").date(washington_afternoon, "fed") == "Sep 16, 2026"
    assert Translator("ja").date(washington_afternoon, "fed") == "2026年9月16日"
    assert Translator("en").date(tokyo_afternoon, "boj") == "Sep 18, 2026"
    assert Translator("ja").date(tokyo_afternoon, "boj") == "2026年9月18日"
    late_evening = datetime(2026, 9, 17, 2, 0, tzinfo=UTC)  # still the 16th in Washington
    assert Translator("ja").date(late_evening, "fed", form="short") == "9月16日"


def test_roles_translate_when_known() -> None:
    assert Translator("ja").role("Governor") == "総裁"
    assert Translator("en").role("Governor") == "Governor"
    assert Translator("ja").role("Special Adviser") == "Special Adviser"
