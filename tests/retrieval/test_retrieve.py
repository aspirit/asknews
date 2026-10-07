from __future__ import annotations

from dataclasses import fields
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
from asknews.retrieval.candidates import Passage
from asknews.retrieval.fusion import Citation, with_answers
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
def test_a_retrieved_question_brings_its_answer(db: Connection) -> None:
    migrate(db)
    ueda = upsert_speaker(db, institution="boj", slug="ueda", name="Kazuo Ueda", role="Governor")
    doc = upsert_document(
        db,
        Document(
            "boj",
            "press_conference",
            "ja",
            "kk240321a",
            "総裁定例記者会見（2024年3月19日）",
            datetime(2024, 3, 19, 6, 30, tzinfo=UTC),
            "https://boj.test/kk240321a.pdf",
            speaker_id=ueda,
        ),
    )
    replace_segments(
        db,
        doc,
        [
            Segment(0, "マイナス金利解除の理由を教えてください。", speaker="Question"),
            Segment(
                1,
                "物価安定の目標の持続的な実現が見通せる状況に至ったためです。",
                speaker="Kazuo Ueda",
            ),
            Segment(2, "次の質問です。為替について伺います。", speaker="Question"),
        ],
    )
    embed_pending(db, HashEmbedder())
    found = retrieve(
        db, HashEmbedder(), "マイナス金利解除の理由は", directory=DIRECTORY, vocab=VOCAB
    )
    positions = [c.passage.position for c in found.citations]
    assert positions[:2] == [0, 1]  # the question, then the governor's answer
    assert found.citations[1].passage.speaker == "Kazuo Ueda"


def _passage(segment_id: int, position: int, speaker: str) -> Passage:
    values: dict[str, object] = {
        "segment_id": segment_id,
        "document_id": 1,
        "position": position,
        "institution": "boj",
        "institution_name": "Bank of Japan",
        "kind": "press_conference",
        "lang": "ja",
        "title": "t",
        "published_at": datetime(2024, 3, 19, tzinfo=UTC),
        "source_url": "u",
        "text": "x",
        "speaker": speaker,
    }
    return Passage(**{f.name: values.get(f.name) for f in fields(Passage)})  # type: ignore[arg-type]


def test_with_answers_inserts_the_following_answer_once() -> None:
    question, answer, other = (
        _passage(1, 0, "Question"),
        _passage(2, 1, "Ueda"),
        _passage(3, 5, "Ueda"),
    )
    cited = [Citation(question, 0.5, ("terms",)), Citation(other, 0.4, ("semantic",))]
    out = with_answers(cited, {1: answer})
    assert [c.passage.segment_id for c in out] == [1, 2, 3]
    assert out[1].paths == ("answer",)
    assert with_answers(out, {1: answer}) == out  # an answer already present is not added again


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


@pytest.mark.integration
def test_named_month_filters_every_path(db: Connection) -> None:
    seed(db)
    inside = retrieve(
        db,
        HashEmbedder(),
        "What did the Fed say about rates in September 2025?",
        directory=DIRECTORY,
        vocab=VOCAB,
    )
    assert inside.citations
    assert all(c.passage.published_at.year == 2025 for c in inside.citations)
    outside = retrieve(
        db,
        HashEmbedder(),
        "What did the Fed say about rates in September 2019?",
        directory=DIRECTORY,
        vocab=VOCAB,
    )
    assert outside.citations == []


@pytest.mark.integration
def test_fulltext_falls_back_to_any_term(db: Connection) -> None:
    seed(db)
    # "zorblax" matches nothing: the all-terms query is empty, the any-term query fills in.
    result = retrieve(
        db,
        HashEmbedder(),
        "Did the Fed mention zorblax or the federal funds rate?",
        directory=DIRECTORY,
        vocab=VOCAB,
    )
    assert any("federal funds rate" in c.passage.text for c in result.citations)
