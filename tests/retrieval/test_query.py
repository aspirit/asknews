from datetime import date
from pathlib import Path

from asknews.domain_config import load_speakers
from asknews.retrieval.gate import load_vocabulary
from asknews.retrieval.query import analyse, detect_language

REPO_ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = load_speakers(REPO_ROOT / "domain" / "speakers.yaml")
VOCAB = load_vocabulary(REPO_ROOT / "domain" / "gate.yaml")
TODAY = date(2026, 10, 7)


def test_language_detection() -> None:
    assert detect_language("What did the Fed decide?") == "en"
    assert detect_language("日銀は何を決めた") == "ja"


def test_english_signals() -> None:
    q = analyse(
        'What did Powell say about "balance sheet runoff" in September 2025?',
        DIRECTORY,
        vocab=VOCAB,
        today=TODAY,
    )
    assert q.lang == "en"
    assert q.phrases == ("balance sheet runoff",)
    assert "powell" in q.speakers
    assert q.window is not None
    assert (q.window.start, q.window.end, q.window.hard) == (
        date(2025, 9, 1),
        date(2025, 9, 30),
        True,
    )
    assert "balance" in q.terms
    assert "what" not in q.terms


def test_recency_is_a_soft_window() -> None:
    q = analyse("What did the Fed decide most recently?", DIRECTORY, vocab=VOCAB, today=TODAY)
    assert q.recency is True
    assert q.window is not None
    assert q.window.hard is False
    assert q.institutions == ("fed",)
    assert "recent" not in q.terms  # time words set the window, they are not searched for
    assert "recently" not in q.terms
    assert "decide" in q.terms


def test_japanese_signals() -> None:
    text = "日銀は2026年9月の決定会合で政策金利をどうした？"
    q = analyse(text, DIRECTORY, vocab=VOCAB, today=TODAY)
    assert q.lang == "ja"
    assert q.institutions == ("boj",)
    assert q.window is not None
    assert (q.window.start, q.window.end) == (date(2026, 9, 1), date(2026, 9, 30))
    assert "政策金利" in q.terms  # 政策金利 as a kanji run
    ueda = analyse("植田総裁の発言", DIRECTORY, vocab=VOCAB, today=TODAY)
    assert "ueda" in ueda.speakers
