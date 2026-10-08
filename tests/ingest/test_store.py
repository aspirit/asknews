from __future__ import annotations

from datetime import UTC, datetime

import pytest
from psycopg.errors import CheckViolation

from asknews.db.connection import Connection
from asknews.db.migrate import migrate
from asknews.embed.base import HashEmbedder
from asknews.embed.worker import embed_pending
from asknews.ingest.store import (
    Document,
    Media,
    Segment,
    institution_id,
    replace_segments,
    segments_hash,
    upsert_document,
    upsert_media,
    upsert_speaker,
)

pytestmark = pytest.mark.integration


def sample_document(
    *, kind: str = "statement", title: str = "FOMC statement", speaker_id: int | None = None
) -> Document:
    return Document(
        institution="fed",
        kind=kind,
        lang="en",
        external_id="20260916",
        title=title,
        published_at=datetime(2026, 9, 16, 18, 0, tzinfo=UTC),
        source_url="https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm",
        body="The Committee decided to lower the target range.",
        meta={"source": "rss"},
        speaker_id=speaker_id,
    )


@pytest.fixture
def conn(db: Connection) -> Connection:
    migrate(db)
    return db


def test_unknown_institution_is_rejected(conn: Connection) -> None:
    with pytest.raises(LookupError):
        institution_id(conn, "nope")


def test_upsert_document_is_idempotent_and_updates(conn: Connection) -> None:
    first = upsert_document(conn, sample_document())
    second = upsert_document(conn, sample_document(title="FOMC statement (revised)"))
    assert first == second
    row = conn.execute("SELECT title, meta FROM documents WHERE id = %s", (first,)).fetchone()
    assert row is not None
    assert row["title"] == "FOMC statement (revised)"
    assert row["meta"] == {"source": "rss"}


def test_replace_segments_skips_unchanged_content(conn: Connection) -> None:
    doc_id = upsert_document(conn, sample_document())
    segments = [
        Segment(0, "First paragraph.", speaker="Chair", start_sec=0.0, end_sec=4.5),
        Segment(1, "Second paragraph."),
    ]
    assert replace_segments(conn, doc_id, segments) is True
    assert replace_segments(conn, doc_id, segments) is False
    assert replace_segments(conn, doc_id, segments[:1]) is True
    rows = conn.execute(
        "SELECT position, text, speaker, start_sec FROM segments WHERE document_id = %s", (doc_id,)
    ).fetchall()
    assert [(r["position"], r["speaker"]) for r in rows] == [(0, "Chair")]
    assert rows[0]["start_sec"] == 0.0


def test_nul_bytes_are_removed_before_writing() -> None:
    from asknews.ingest.store import clean_text

    assert clean_text("a" + chr(0) + "b") == "ab"
    assert clean_text("3" + chr(0x2011) + "1/2 to 3" + chr(0x2010) + "3/4") == "3-1/2 to 3-3/4"
    assert clean_text(None) is None


def test_segments_hash_depends_on_text_speaker_and_order() -> None:
    a = [Segment(0, "x", speaker="A"), Segment(1, "y")]
    assert segments_hash(a) == segments_hash(list(a))
    assert segments_hash(a) != segments_hash([Segment(0, "x", speaker="B"), Segment(1, "y")])
    assert segments_hash(a) != segments_hash(list(reversed(a)))


def test_media_and_speaker_upserts(conn: Connection) -> None:
    speaker = upsert_speaker(
        conn, institution="fed", slug="powell", name="Jerome Powell", role="Chair"
    )
    again = upsert_speaker(
        conn, institution="fed", slug="powell", name="Jerome H. Powell", role="Chair"
    )
    assert speaker == again
    doc_id = upsert_document(conn, sample_document(kind="press_conference", speaker_id=speaker))
    upsert_media(conn, doc_id, Media("brightcove", "6379710022112", 3132))
    upsert_media(conn, doc_id, Media("brightcove", "6379710022112", 3133))
    row = conn.execute(
        "SELECT m.duration_sec, s.name FROM media m JOIN documents d ON d.id = m.document_id "
        "JOIN speakers s ON s.id = d.speaker_id WHERE m.document_id = %s",
        (doc_id,),
    ).fetchone()
    assert row is not None
    assert (row["duration_sec"], row["name"]) == (3133, "Jerome H. Powell")


def test_timing_corrections_preserve_segment_ids_and_embeddings(conn: Connection) -> None:
    doc_id = upsert_document(conn, sample_document())
    replace_segments(conn, doc_id, [Segment(0, "A statement.", start_sec=10, end_sec=20)])
    assert embed_pending(conn, HashEmbedder()) == 1
    before = conn.execute("SELECT * FROM segment_embeddings").fetchall()

    for start, end in [(12.5, 21.0), (None, None), (0.0, 5.0)]:
        segments = [Segment(0, "A statement.", start_sec=start, end_sec=end)]
        assert replace_segments(conn, doc_id, segments)
        assert not replace_segments(conn, doc_id, segments)
        assert conn.execute("SELECT start_sec, end_sec FROM segments").fetchone() == {
            "start_sec": start,
            "end_sec": end,
        }
        assert conn.execute("SELECT * FROM segment_embeddings").fetchall() == before
        assert embed_pending(conn, HashEmbedder()) == 0


def test_text_revision_replaces_vectors_and_can_be_retried(conn: Connection) -> None:
    doc_id = upsert_document(conn, sample_document())
    replace_segments(conn, doc_id, [Segment(0, "Original statement.")])
    assert embed_pending(conn, HashEmbedder()) == 1
    old = conn.execute("SELECT segment_id FROM segment_embeddings").fetchone()
    revised = [Segment(0, "Revised statement."), Segment(1, "New explanation.")]
    assert replace_segments(conn, doc_id, revised)
    assert conn.execute("SELECT * FROM segment_embeddings").fetchall() == []
    assert embed_pending(conn, HashEmbedder()) == 2
    assert old not in conn.execute("SELECT segment_id FROM segment_embeddings").fetchall()
    assert not replace_segments(conn, doc_id, revised)
    assert embed_pending(conn, HashEmbedder()) == 0
    assert conn.execute("SELECT count(*) AS n FROM documents").fetchone() == {"n": 1}


@pytest.mark.parametrize("same_text", [False, True])
def test_failed_replacement_rolls_back_text_timing_and_vectors(
    conn: Connection, same_text: bool
) -> None:
    doc_id = upsert_document(conn, sample_document())
    original = [Segment(0, "Original.", start_sec=0, end_sec=10), Segment(1, "Second.")]
    replace_segments(conn, doc_id, original)
    embed_pending(conn, HashEmbedder())
    before = conn.execute("SELECT * FROM segments ORDER BY position").fetchall()
    vectors = conn.execute("SELECT * FROM segment_embeddings ORDER BY segment_id").fetchall()
    bad = [
        Segment(0, "Original." if same_text else "Revision.", start_sec=1, end_sec=11),
        Segment(1, "Second.", start_sec=-1),
    ]
    with pytest.raises(CheckViolation):
        replace_segments(conn, doc_id, bad)
    assert conn.execute("SELECT * FROM segments ORDER BY position").fetchall() == before
    assert (
        conn.execute("SELECT * FROM segment_embeddings ORDER BY segment_id").fetchall() == vectors
    )
    assert not replace_segments(conn, doc_id, original)
