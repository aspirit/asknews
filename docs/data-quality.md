# Data quality: unreadable source text

A RAG answer can only be as good as the passages it retrieves. This note
records one class of defect found in the archive in October 2026, how it was
measured, what was changed, and how to check that it stays fixed.

## What was found

A scan of every stored document body turned up text that was not text:

| Defect | Documents | Cause |
|---|---|---|
| Bank of Japan minutes, 2011 to 2017, read as letters from random scripts | 84 | The PDFs' fonts have no usable Unicode map; pypdf returns one wrong code point per glyph |
| Bank of Japan speeches with control characters or the same garbling | 15 | As above, plus control characters leaked by the text layer |
| Feed items whose body was the raw bytes of a PDF (`%PDF-1.6 ...`) | 5 | The linked file was a PDF, read as an HTML page |

Together they had been split into about 1,600 passages and embedded, so they
were retrievable by the Ask page, and shown as the full text of the document
page. None had been summarised.

## What changed

- **A readability gate** (`ingest/textquality.py`). Text is readable when
  it has at least fifty letters, at least nine in ten of them in the Latin,
  Greek, kana or Han ranges, and almost no control or replacement
  characters. A file's bytes and a broken text layer both fail it.
- **Two PDF readers** (`ingest/pdf.py`). pypdf's text is used when it passes
  the gate; otherwise PDFium's reading is tried. PDFium decodes the old
  minutes correctly and opens the encrypted ministry files pypdf cannot.
- **No text is better than wrong text.** A document neither reader can read
  is still stored, as a dated entry linking to the original, with no body
  and no passages (`meta.text = "unusable"`). Its page says the text could
  not be read and points to the source, in both languages.
- **Feed links are typed by content**, not by extension: a response is a
  PDF when its Content-Type says so or its first bytes are `%PDF-`. With no
  readable text the feed's own description is stored instead.
- Control characters are removed on the way into the database.

No OCR: no document in the archive is a scan without a text layer, and
OCR would add a heavy dependency for no current gain.

## Result

After re-ingesting the Bank of Japan minutes and speeches and the feeds:

| | Before | After |
|---|---|---|
| Bodies failing the readability gate | 104 | 0 |
| Recovered with the second reader | | 101 |
| Stored as unreadable, link only | | 3 (speeches, 2012 to 2013) |

The recovered passages are re-embedded by the regular `asknews embed` step.

## Checking it

Tests cover a plain PDF, a text layer only the second reader can decode, a
file with no readable layer, a PDF served with an HTML Content-Type, a
missing page, and the unreadable-document page.

To scan a database, run the gate over every stored body:

```sh
uv run python - <<'EOF'
import psycopg
from asknews.ingest.textquality import readable
from asknews.settings import get_settings

QUERY = """
    SELECT id, body FROM documents
     WHERE body IS NOT NULL AND length(body) >= 200
       AND coalesce(meta->>'text', 'page') = 'page'
"""
with psycopg.connect(get_settings().database_dsn) as conn:
    print([doc_id for doc_id, body in conn.execute(QUERY) if not readable(body)])
EOF
```

An empty list is the expected answer.
