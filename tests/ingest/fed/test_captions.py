from asknews.ingest.fed.captions import fill_gaps, repair_cues, restore_dashes
from asknews.ingest.vtt import Cue

TRANSCRIPT = (
    "Good day. In the meeting just concluded, the FOMC decided to raise the target range\n"
    "for the federal funds rate by a ¼ percentage point to 3¾ to 4 percent, in support of\n"
    "the dual mandate. While uncertainty remains elevated—owing, in part, to geopolitical\n"
    "developments—domestic spending has held up. The jobless—the jobless rate remains low.\n"
    "New hiring, private-sector earnings, business capital investment—each of these markers\n"
    "has improved. The Committee will act, as appropriate, § 2A, to sustain the expansion."
)


def test_gap_takes_the_glyph_glued_to_the_word() -> None:
    assert fill_gaps("point to 3  to 4 percent,", TRANSCRIPT) == "point to 3¾ to 4 percent,"


def test_gap_takes_a_separate_token() -> None:
    assert fill_gaps("as appropriate,  2A, to sustain", TRANSCRIPT) == (
        "as appropriate, § 2A, to sustain"
    )


def test_unmatched_gap_collapses() -> None:
    assert fill_gaps("nothing  like this", TRANSCRIPT) == "nothing like this"
    assert repair_cues([Cue(0.0, 1.0, "trailing gap  ")], TRANSCRIPT)[0].text == "trailing gap"


def test_dashes_return_only_where_the_transcript_has_them() -> None:
    text = "The jobless-the jobless rate; private-sector earnings; elevated-owing, in part"
    assert restore_dashes(text, TRANSCRIPT) == (
        "The jobless—the jobless rate; private-sector earnings; elevated—owing, in part"
    )


def test_repair_cues_without_transcript_only_collapses_gaps() -> None:
    cues = [Cue(0.0, 1.0, "to 3  to 4 percent"), Cue(1.0, 2.0, "investment-each marker")]
    repaired = repair_cues(cues, None)
    assert [c.text for c in repaired] == ["to 3 to 4 percent", "investment-each marker"]
    assert repaired[0].start == 0.0
    with_pdf = repair_cues(cues, TRANSCRIPT)
    assert [c.text for c in with_pdf] == ["to 3¾ to 4 percent", "investment—each marker"]
