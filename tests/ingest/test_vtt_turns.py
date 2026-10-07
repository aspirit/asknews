from __future__ import annotations

from pathlib import Path

import pytest

from asknews.ingest.turns import normalise_speaker, segment_cues, speaker_turns
from asknews.ingest.vtt import Cue, VttError, parse_timestamp, parse_vtt

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "fed_presconf_captions.vtt"


@pytest.fixture(scope="module")
def cues() -> list[Cue]:
    return parse_vtt(FIXTURE.read_text("utf-8"))


def test_parse_timestamp_handles_both_forms() -> None:
    assert parse_timestamp("00:04.356") == pytest.approx(4.356)
    assert parse_timestamp("01:02:03.5") == pytest.approx(3723.5)
    with pytest.raises(VttError):
        parse_timestamp("5")


def test_parse_vtt_reads_cues_and_joins_lines(cues: list[Cue]) -> None:
    assert len(cues) == 29
    assert cues[0].start == pytest.approx(4.356)
    assert cues[0].text == "Transcript of Chair Powell's Press Conference September 17,"
    assert cues[-1].end > cues[0].end
    assert all(" \n" not in c.text and "  " not in c.text for c in cues)


def test_parse_vtt_rejects_non_vtt() -> None:
    with pytest.raises(VttError):
        parse_vtt("hello")


def test_parse_vtt_strips_tags_and_entities() -> None:
    doc = "WEBVTT\n\n00:01.000 --> 00:02.000\n<c.yellow>Rates &amp; growth</c>\n"
    assert parse_vtt(doc)[0].text == "Rates & growth"


def test_speaker_turns_follow_labels(cues: list[Cue]) -> None:
    turns = speaker_turns(cues)
    assert [t.speaker for t in turns] == ["Chair Powell", "Chris Rugaber"]
    assert turns[0].text.startswith("Good afternoon.")
    assert turns[1].text.startswith("Great. Thank you.")
    assert turns[0].start == pytest.approx(14.526)
    assert turns[1].start > turns[0].end
    assert "CHAIR POWELL" not in turns[0].text


def test_normalise_speaker() -> None:
    assert normalise_speaker("CHAIR POWELL") == "Chair Powell"
    assert normalise_speaker("MICHAEL MCKEE") == "Michael McKee"
    assert normalise_speaker("CATHERINE O'HAGAN") == "Catherine O'Hagan"


def test_acronyms_are_not_speakers() -> None:
    doc = [Cue(0, 1, "CHAIR POWELL. We discussed it at the FOMC. The outlook improved.")]
    assert [t.speaker for t in speaker_turns(doc)] == ["Chair Powell"]


def test_segments_respect_speakers_and_size(cues: list[Cue]) -> None:
    segments = segment_cues(cues, max_chars=300)
    assert [s.position for s in segments] == list(range(len(segments)))
    assert all(len(s.text) <= 300 or "." not in s.text[:-1] for s in segments)
    assert {s.speaker for s in segments} == {"Chair Powell", "Chris Rugaber"}
    assert all(s.start_sec is not None and s.end_sec is not None for s in segments)
    starts = [s.start_sec for s in segments if s.start_sec is not None]
    assert starts == sorted(starts)
    assert all(s.start_sec <= s.end_sec for s in segments if s.start_sec and s.end_sec)
    joined = " ".join(s.text for s in segments)
    assert "benefit of the American people" in joined


def test_single_long_sentence_stays_whole() -> None:
    doc = [Cue(0, 1, "CHAIR POWELL. " + "word " * 100 + "end")]
    segments = segment_cues(doc, max_chars=50)
    assert len(segments) == 1


def test_caption_line_markers_are_stripped() -> None:
    doc = "WEBVTT\n\n00:01.000 --> 00:02.000\n- Good afternoon.\n- I am pleased to join you.\n"
    assert parse_vtt(doc)[0].text == "Good afternoon. I am pleased to join you."


def test_colon_after_speaker_label_is_accepted() -> None:
    doc = [Cue(0, 1, "CHAIRMAN BERNANKE: Good afternoon. Before we get to questions, a summary.")]
    turns = speaker_turns(doc)
    assert [t.speaker for t in turns] == ["Chairman Bernanke"]
    assert turns[0].text.startswith("Good afternoon.")
