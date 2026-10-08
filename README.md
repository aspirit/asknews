# asknews

Bilingual question answering over the public communications of central banks.

[Live application](https://asknews.jp/) | [Local walkthrough](docs/walkthrough.md) |
[Architecture](docs/architecture.md) | [Retrieval evaluation](docs/evaluation.md)

The archive covers what the Federal Reserve publishes in English and what the
Bank of Japan publishes in Japanese: press conference videos and transcripts,
policy statements, meeting minutes, speeches and outlook reports, plus the
release feeds of the ECB, the BIS, the U.S. BEA and Japan's Ministry of
Finance and Financial Services Agency. Ask a question in either language and
get an answer built only from retrieved passages, each one cited back to the
official document or to a moment in the video.

## What it does

- **Ask.** Hybrid retrieval (full text, Japanese substring matching, exact
  phrases, dense vectors) over 90,000 passages; a cited answer whose every
  citation is checked against the passages; refusals for questions outside
  the archive; a daily spend budget and per-client rate limits.
- **Archive.** Latest releases, every press conference with its transcript
  (timestamped where the publisher provides captions), policy meetings
  assembled from the documents each one produced, rankings of speakers,
  publishers and topics computed from the material itself, and briefs.
- **Two languages.** English or Japanese interface; documents stay in their
  original language with a badge; generated titles, summaries and briefs are
  labelled as such.

## Principles

- **Sources first.** Retrieval results are shown before the generated answer,
  and the answer may only use what was retrieved.
- **Declines out of scope.** Questions unrelated to the archive are refused
  rather than answered from general knowledge.
- **No re-hosting of video.** Official players are embedded and transcripts
  link to timestamps where the source provides them.
- **Generated content is labelled.** Nothing produced by a model is shown as
  the publisher's own text.

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/), Python 3.13 (uv installs it
on demand) and Docker for the local PostgreSQL instance.

For a fixed demonstration without API keys or publisher access:

```sh
uv sync --locked --all-groups
make demo
```

Open <http://127.0.0.1:8001>. This uses six explicitly synthetic documents,
hash embeddings and scripted answers in a separate local database. See the
[walkthrough](docs/walkthrough.md) for examples, limitations and shutdown.
`make demo-eval` runs the fixture retrieval checks and writes a JSON report.

To ingest real publications into the regular development database instead:

```sh
uv sync --all-groups
cp .env.example .env
docker compose up -d --wait              # PostgreSQL 17 with pgvector
uv run asknews migrate
uv run asknews ingest fed-presconf --limit 5
uv run asknews ingest boj-kaiken --since 2026
uv run asknews ingest feeds --days 7
uv run asknews topics                    # topic membership for the rankings
uv run asknews embed                     # the hashing stand-in: no model download
ASKNEWS_LLM=scripted uv run uvicorn asknews.api.app:app --reload
```

Open <http://127.0.0.1:8000>. With `ASKNEWS_LLM=scripted` the Ask page
returns a canned answer over real retrieved sources; set `ANTHROPIC_API_KEY`
and drop the variable for real answers. `make check` runs lint, type checks
and tests; database-backed tests use `TEST_DATABASE_URL` from `.env.example`
and are skipped when it is unset.

## Commands

| Command | Purpose |
| --- | --- |
| `asknews migrate` | Apply pending SQL migrations |
| `asknews ingest fed-presconf` / `fed-releases` | Federal Reserve press conferences; statements, minutes, speeches |
| `asknews ingest boj-kaiken` / `boj-releases` / `boj-videos` | Bank of Japan press conferences; statements, minutes, opinions, outlook, speeches; YouTube videos |
| `asknews ingest feeds` | Other publishers' RSS feeds (configured in `domain/feeds.yaml`) |
| `asknews topics` | Tag documents with the topics they mention (`domain/topics.yaml`) |
| `asknews titles` | Titles of formulaic documents in the other language, written from patterns |
| `asknews embed` | Embed segments that lack a vector |
| `asknews summarize` / `asknews briefs daily` / `asknews briefs meetings` | Generated summaries, translations and briefs |
| `asknews daily` | All of the above, incrementally, for a scheduled run |
| `asknews eval` | Retrieval regression cases in `domain/eval_cases.yaml` |
| `asknews demo-seed` | Synthetic fixture data in the isolated local `asknews_demo` database |

## HTTP API

The pages are server-rendered; the JSON API behind them is small.

| Endpoint | What it returns |
|---|---|
| `POST /api/ask` with `{"q": "..."}` | An answer with numbered citations, or why there is none (`out_of_scope`, `no_sources`, `rate`, `budget`). A POST so that a crawler following a link can never run a question |
| `GET /api/ask/citations?q=` | The passages a question would be answered from, without calling the model |
| `GET /api/documents`, `/api/documents/{id}` | Catalogue entries and one document with its segments |
| `GET /api/meetings` | Policy meetings with their statement, minutes, decision and votes |
| `GET /api/rankings?days=` | Speakers, publishers and topics over a period |
| `GET /api/briefs`, `/api/briefs/{id}` | Daily and meeting briefs |

The OpenAPI document is at `/api/openapi.json` and the interactive reference at `/api/docs`.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | the local compose database | PostgreSQL with pgvector |
| `ASKNEWS_EMBEDDER` | `hash` | `hash` (stand-in), `remote` (embedding service), `local` (in-process model) |
| `ASKNEWS_EMBED_URL` | `http://127.0.0.1:8201` | Embedding service when `remote` |
| `ASKNEWS_EMBED_MODEL` | `google/embeddinggemma-300m` | Model for `local` and for the service; `intfloat/multilingual-e5-base` needs no token |
| `ASKNEWS_LLM` | `claude` | `claude` or `scripted` |
| `ASKNEWS_LLM_MODEL` | `claude-opus-5-5` | Model for answers |
| `ASKNEWS_SUMMARY_MODEL` | `claude-sonnet-5-5` | Model for summaries and briefs |
| `ASKNEWS_DAILY_BUDGET_USD` | `3` | Cap on the estimated model spend over any 24 hours, answers and generation together; generation stops at half of it |
| `ASKNEWS_CLIENT_SALT` | `change-me` | Salt for the daily client hash used in rate limiting |
| `ASKNEWS_SITE_URL` | `https://asknews.jp` | Public origin for canonical and alternate-language links |
| `ANTHROPIC_API_KEY` | | Read by the Anthropic SDK |
| `YOUTUBE_API_KEY` | | Optional; without it only the channel feed's latest videos are attached |

## Documentation

- [docs/architecture.md](docs/architecture.md): components, data model, retrieval, answering, design choices
- [docs/data-sources.md](docs/data-sources.md): every publisher, how it is accessed, and its terms
- [docs/pages.md](docs/pages.md): what each page shows
- [docs/deployment.md](docs/deployment.md): one-host deployment with Docker Compose and nginx
- [docs/walkthrough.md](docs/walkthrough.md): no-key local demonstration and its boundaries
- [docs/evaluation.md](docs/evaluation.md): evidence checks, retrieval comparisons and metrics
- [docs/decisions.md](docs/decisions.md): storage, retrieval and budget trade-offs
- [docs/data-quality.md](docs/data-quality.md): unreadable source text, how it was found and fixed

## License

MIT. Source documents remain under the terms of their publishers, recorded
in [docs/data-sources.md](docs/data-sources.md).
