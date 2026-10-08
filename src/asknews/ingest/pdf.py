"""Text extraction from PDF files.

pypdf reads the common case. Some publishers' older files carry a text
layer pypdf cannot decode (fonts without a Unicode map, which come out as
letters from random scripts) or an encryption it cannot open; for those
PDFium's reading is used. A file neither can read yields no text, and the
caller stores the document as a link to the original.
"""

from __future__ import annotations

import io
import logging
import re

import pypdfium2 as pdfium
from pypdf import PdfReader

from asknews.ingest.textquality import readable, strip_control

LOG = logging.getLogger(__name__)

_SPACES = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def pdf_text(data: bytes) -> str:
    """The text layer of every page, lightly normalised; empty when no
    reader produces readable text."""
    text = _normalise(_with_pypdf(data))
    if readable(text):
        return text
    alternative = _normalise(_with_pdfium(data))
    if readable(alternative):
        return alternative
    LOG.warning("no readable text layer (%d bytes)", len(data))
    return ""


def _with_pypdf(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:  # pypdf raises its own hierarchy plus DependencyError
        LOG.debug("pypdf could not read the file: %s", exc)
        return ""


def _with_pdfium(data: bytes) -> str:
    try:
        document = pdfium.PdfDocument(data)
    except Exception as exc:
        LOG.debug("PDFium could not open the file: %s", exc)
        return ""
    try:
        return "\n\n".join(page.get_textpage().get_text_range() for page in document)
    finally:
        document.close()


def _normalise(text: str) -> str:
    text = strip_control(text)
    text = _SPACES.sub(" ", text)
    return _BLANK_LINES.sub("\n\n", text).strip()
