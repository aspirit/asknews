from __future__ import annotations

import pytest

from asknews.ingest import pdf
from asknews.ingest.textquality import readable
from tests.ingest.pdfs import minimal_pdf
from tests.ingest.test_textquality import ENGLISH, GARBLED

LINES = [
    "The Governing Council decided to keep the three key interest rates unchanged.",
    "Inflation is expected to settle at around the two percent target over the medium term.",
    "The Council will follow a data-dependent and meeting-by-meeting approach.",
]


def test_a_plain_file_is_read_by_the_first_reader() -> None:
    text = pdf.pdf_text(minimal_pdf(LINES))
    assert "three key interest rates unchanged" in text
    assert readable(text)


def test_the_second_reader_takes_over_when_the_first_cannot_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pdf, "_with_pypdf", lambda _data: GARBLED)
    monkeypatch.setattr(pdf, "_with_pdfium", lambda _data: ENGLISH + "\r\n" + ENGLISH)
    text = pdf.pdf_text(b"%PDF-")
    assert text.startswith(ENGLISH)
    assert "\r" not in text


def test_no_readable_layer_yields_nothing() -> None:
    assert pdf.pdf_text(b"not a pdf at all") == ""
