# Architecture

```
publishers --ingest--> documents / segments --embed--> segment_embeddings
                            |                                 |
                            +------ retrieval (lexical + dense, fused) <-- question
                                           |
                                 answer (Claude, cited, checked) --> ask_requests
                                           |
                 pages & JSON API <--------+      generate: summaries, translations, briefs
```

## Components

| Package | Role |
| --- | --- |
| `asknews.ingest` | One module per source. Shared: a cached HTTP fetcher, an HTML paragraph extractor, PDF text extraction, caption parsing, and store helpers that upsert documents and replace segments only when content changed. |
| `asknews.db` | Connection helper and forward-only SQL migrations with checksums. |
| `asknews.embed` | The `Embedder` protocol; a hashing stand-in for tests and development; a remote client for the embedding service; EmbeddingGemma or multilingual-e5 behind the `model` extra; the worker that embeds pending segments. |
| `asknews.retrieval` | Query analysis, lexical and dense candidate paths, reciprocal-rank fusion with boosts, and the domain gate. |
| `asknews.answer` | Prompting, the Claude client, reply checking, request logging and the guards (budget, rate). |
| `asknews.archive` | Read-side queries: lists, detail, meetings assembled from documents, rankings, briefs. |
| `asknews.topics` | Tags each document once with the topics whose terms it contains, so the rankings read a small table. |
| `asknews.generate` | Summaries with translations, and daily and meeting briefs. |
| `asknews.api`, `asknews.web` | FastAPI application: JSON endpoints under `/api`, Jinja2 pages, static files, locales. |
| `domain/` | Everything that names the real world: speakers, gate vocabulary, topics, feeds, featured videos, sample questions, evaluation cases. |

## Data model

`institutions` -> `documents` (kind, language, external id, title, date, URL,
body, generated summary) -> `segments` (text, speaker, start and end second,
language, generated tsvector) -> `segment_embeddings` (one vector per segment,
with the model name). `media` attaches a provider video to a document;
`speakers` names the principal speaker; `document_translations`, `briefs`
and `ask_requests` hold generated content and usage.

`document_topics` holds each document's topics, computed once from the
term list and refreshed when that list changes.

Policy meetings are not a table. They are read off the documents: the
Federal Reserve's statement, press conference and minutes share the
meeting date in their identifiers; the Bank of Japan's documents share a
publication date or name the meeting in their metadata.

## Retrieval

Two paths see every question. **Lexical** search is what finds names,
numbers and exact phrases: PostgreSQL full text with the English
configuration for English, substring matching of kanji and katakana runs
over a trigram index for Japanese, and exact matching of quoted phrases in
any language. **Dense** search runs over the segment vectors (pgvector,
HNSW, cosine). A month or year named in the question filters both paths
inside SQL. Results are fused by reciprocal rank, boosted when the passage
matches a named publisher, speaker or period, capped at two passages per
document, and cut to twelve.

The **domain gate** declines questions that name nothing in the vocabulary,
no publisher and no known speaker, unless lexical search still finds a
quoted phrase or a passage containing every content term.

## Answering

The model receives only the question and the numbered passages, with the
instruction to cite each factual sentence as `[n]` and to reply as JSON.
The reply is checked: citations pointing at no passage are removed,
paragraphs left without a citation are dropped, and the sources actually
used are renumbered in order of first use. Before any of that, the daily
spend cap and per-client hourly and daily limits are applied; after it,
the request is logged with tokens, estimated cost and latency. The cap is a
rolling 24 hours over answers and generation together (`ask_requests` plus
`llm_usage`); summaries and briefs stop at half of it so that questions can
still be answered after the daily job has run.

## Generated content

Summaries, title and summary translations, daily briefs and meeting briefs
are produced by a cheaper bulk model, validated (a brief point must cite a
real document) and stored with the model name. The interface marks every
generated element with the same AI label; official text is never rewritten.

## Languages

The interface is English or Japanese, chosen by the path: English at the
root, Japanese under `/ja`, each page linking its counterpart with `hreflang`. Documents keep
their original language and show a badge; translated titles are labelled.
Interface strings live in `src/asknews/locales`; source code, comments and
documentation are English, enforced by a test.

## Why these choices

- **No video hosting.** Publishers' players are embedded and seeked from transcript timestamps, so the archive respects their terms and stores no media.
- **Captions as transcripts.** The Federal Reserve's caption track is the transcript text with speaker labels, which yields timestamped, attributed segments without speech recognition.
- **One database.** Relational data, full text, trigrams and vectors in PostgreSQL keep the stack to one store and let a period filter apply inside every retrieval path.
- **An embedder interface.** Development and tests run on a deterministic stand-in; production runs a real model in its own container; the model can change behind the interface.
