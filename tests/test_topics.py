from datetime import UTC, datetime
from pathlib import Path

from asknews.db.connection import Connection
from asknews.db.migrate import migrate
from asknews.domain_config import Topic, load_topics, topics_version
from asknews.ingest.store import Document, upsert_document
from asknews.topics import match_topics, tag_pending

REPO_ROOT = Path(__file__).resolve().parents[1]
TOPICS = load_topics(REPO_ROOT / "domain" / "topics.yaml")


def test_terms_match_case_insensitively_in_both_scripts() -> None:
    assert match_topics("The Committee lowered the Federal Funds Rate.", TOPICS) == ["policy-rate"]
    assert match_topics("本日、政策金利を据え置いた。", TOPICS) == ["policy-rate"]
    assert match_topics("Nothing relevant here.", TOPICS) == []


def test_version_depends_on_terms_only() -> None:
    a = Topic("x", {}, {"en": ["a"]})
    assert topics_version([a]) != topics_version([Topic("x", {}, {"en": ["b"]})])
    assert topics_version([a]) == topics_version([Topic("x", {"en": "Label"}, {"en": ["a"]})])


def _rows(db: Connection, document_id: int) -> list[str]:
    rows = db.execute(
        "SELECT topic FROM document_topics WHERE document_id = %s ORDER BY topic", (document_id,)
    ).fetchall()
    return [str(r["topic"]) for r in rows]


def test_tagging_is_incremental_and_follows_the_term_list(db: Connection) -> None:
    migrate(db)
    doc = upsert_document(
        db,
        Document(
            "fed",
            "statement",
            "en",
            "20260128",
            "Federal Reserve issues FOMC statement",
            datetime(2026, 1, 28, 19, tzinfo=UTC),
            "https://fed.test/s",
            body="The target range for the federal funds rate is unchanged; inflation eased.",
        ),
    )
    assert tag_pending(db, TOPICS) == 1
    assert tag_pending(db, TOPICS) == 0
    assert _rows(db, doc) == ["inflation", "policy-rate"]

    narrower = (Topic("rates", {}, {"en": ["target range"]}),)
    assert tag_pending(db, narrower) == 1  # a new term list re-tags every document
    assert _rows(db, doc) == ["rates"]
