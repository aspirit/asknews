"""Text extraction from PDF files."""

from __future__ import annotations

import io
import re

from pypdf import PdfReader

_SPACES = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def pdf_text(data: bytes) -> str:
    """Concatenate the text layer of every page, lightly normalised."""
    reader = PdfReader(io.BytesIO(data))
    pages = [(page.extract_text() or "") for page in reader.pages]
    text = "\n\n".join(pages)
    text = _SPACES.sub(" ", text)
    return _BLANK_LINES.sub("\n\n", text).strip()


def pdf_page_count(data: bytes) -> int:
    return len(PdfReader(io.BytesIO(data)).pages)
