from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from asknews.db.connection import Connection
from asknews.db.migrate import migrate
from asknews.domain_config import load_speakers
from asknews.embed.base import HashEmbedder
from asknews.embed.worker import embed_pending
from asknews.ingest.store import (
    Document,
    Segment,
    replace_segments,
    upsert_document,
    upsert_speaker,
)
from asknews.retrieval import retrieve
from asknews.retrieval.gate import load_vocabulary, mentions_domain
from asknews.retrieval.query import analyse

REPO_ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = load_speakers(REPO_ROOT / "domain" / "speakers.yaml")
VOCAB = load_vocabulary(REPO_ROOT / "domain" / "gate.yaml")

JA_STATEMENT = (
    "日本銀行は、本日、政策金利を"
    "1.25%程度とすることを決定した。"
)  # 日本銀行は、本日、政策金利を1.25%程度とすることを決定した。
JA_OTHER = "天気は晴れです。"  # 天気は晴れです。


def seed(db: Connection) -> None:
    migrate(db)
    powell = upsert_speaker(
        db, institution="fed", slug="powell", name="Jerome H. Powell", role="Chair"
    )
    fed = upsert_document(
        db,
        Document(
            "fed",
            "press_conference",
            "en",
            "20250917",
            "FOMC Press Conference September 17, 2025",
            datetime(2025, 9, 17, 18, 30, tzinfo=UTC),
            "https://fed.test/pc",
            speaker_id=powell,
        ),
    )
    replace_segments(
        db,
        fed,
        [
            Segment(
                0,
                "We decided to lower the target range for the federal funds rate by a quarter.",
                speaker="Chair Powell",
                start_sec=30.0,
                end_sec=40.0,
            ),
            Segment(
                1,
                "Balance sheet runoff continues at the current pace.",
                speaker="Chair Powell",
                start_sec=600.0,
                end_sec=610.0,
            ),
        ],
    )
    boj = upsert_document(
        db,
        Document(
            "boj",
            "statement",
            "ja",
            "k260918a",
            "金融市場調節方針の変更について",
            datetime(2026, 9, 18, 3, tzinfo=UTC),
            "https://boj.test/s",
        ),
    )
    replace_segments(db, boj, [Segment(0, JA_STATEMENT), Segment(1, JA_OTHER)])
    other = upsert_document(
        db,
        Document(
            "bea",
            "release",
            "en",
            "gdp",
            "Gross Domestic Product, Second Quarter 2026",
            datetime(2026, 8, 28, 12, tzinfo=UTC),
            "https://bea.test/gdp",
        ),
    )
    replace_segments(
        db,
        other,
        [Segment(0, "Real gross domestic product increased at an annual rate of 2.1 percent.")],
    )
    embed_pending(db, HashEmbedder())


@pytest.mark.integration
def test_english_fulltext_and_speaker_boost(db: Connection) -> None:
    seed(db)
    result = retrieve(
        db,
        HashEmbedder(),
        "What did Powell say about the federal funds rate?",
        directory=DIRECTORY,
        vocab=VOCAB,
    )
    assert result.in_domain is True
    assert result.citations
    top = result.citations[0].passage
    assert top.institution == "fed"
    assert "federal funds rate" in top.text
    assert top.start_sec == 30.0
    assert top.media_provider is None


@pytest.mark.integration
def test_quoted_phrase_wins(db: Connection) -> None:
    seed(db)
    result = retrieve(
        db,
        HashEmbedder(),
        'Where was "balance sheet runoff" mentioned?',
        directory=DIRECTORY,
        vocab=VOCAB,
    )
    assert result.citations[0].passage.position == 1
    assert "phrase" in result.citations[0].paths


@pytest.mark.integration
def test_japanese_terms_use_substring_matching(db: Connection) -> None:
    seed(db)
    question = "日銀は政策金利をどうしたか"  # 日銀は政策金利をどうしたか
    result = retrieve(db, HashEmbedder(), question, directory=DIRECTORY, vocab=VOCAB)
    assert result.in_domain is True
    assert result.citations[0].passage.text == JA_STATEMENT
    assert result.citations[0].passage.lang == "ja"


@pytest.mark.integration
def test_out_of_domain_question_is_declined(db: Connection) -> None:
    seed(db)
    result = retrieve(
        db, HashEmbedder(), "What is a good recipe for ramen?", directory=DIRECTORY, vocab=VOCAB
    )
    assert result.in_domain is False
    assert result.citations == []


def test_gate_vocabulary() -> None:
    def in_domain(text: str) -> bool:
        return mentions_domain(analyse(text, DIRECTORY, vocab=VOCAB), VOCAB, DIRECTORY)

    assert in_domain("Why did inflation rise?")
    assert in_domain("円安の影響は")
    assert not in_domain("Best ramen in Tokyo")
