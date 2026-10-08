# Local walkthrough

The public application is at https://asknews.jp/. The local demo below is a
separate, synthetic corpus: six documents and eleven passages, not a download
of the live database. Titles begin with `[Demo]`, and original-source links use
the reserved `example.invalid` domain. They are deliberately not official citations.

## Start

From the repository root, with uv and Docker installed:

```sh
uv sync --locked --all-groups
make demo
```

Open http://127.0.0.1:8001. No `.env` file, API key, source fetch or model download
is needed. Installing Python dependencies and pulling the PostgreSQL image need
network access the first time. The database has its own volume and listens only
on localhost at port 55432; the application also binds only to localhost.
`make demo DEMO_PORT=8002 DEMO_DB_PORT=55433` selects other ports when needed.

The command explicitly selects hash embeddings and a scripted answer, overriding
those settings in an existing environment. It does not call a paid model. The
scripted answer is generic English even on the Japanese interface; it exercises
citations and response handling, not translation or answer quality. Any cost shown
in request records is a test estimate, not a charge.

## Five-minute path

1. Visit `/archive/latest`. Filter by publisher and document type, then switch
   to Japanese. The interface changes language; source text does not.
2. Open the demo FOMC statement and inspect its paragraphs and source link.
   The document id is printed by `demo-seed`; ids are not hard-coded in this guide.
3. At `/ask`, enter `What did the Fed statement say about labor market conditions
   in September 2025?`. Submit it, then follow the answer's citation to its source.
   Existing homepage suggestions target the live corpus, not these six fixtures.
4. Open the demo press conference. Its fixture timestamps exercise passage
   links, but no video is attached. Official player playback requires the real
   archive and network access; this demo does not pretend to test it offline.
5. Ask `What was the Fed decision in 2001?`, then `How do I bake a birthday cake?`.
   Compare missing evidence with a domain refusal. Open the Japanese demo report
   to see the missing-text state instead of fabricated body text.

## Check and stop

```sh
make demo-eval
# Inspect data/eval/demo.json: seven fixture cases, with no answer-model calls.
make demo-down
```

Stop the foreground web server with Ctrl+C before stopping the database. The
database volume remains for the next run. Repeated seeding preserves document ids
and existing vectors. The seed command refuses remote destinations, other database
names and archives containing non-demo documents; it never truncates a database.

For real retrieval evaluation use a real corpus, the production embedding model
and evidence annotations as described in [evaluation.md](evaluation.md). These
fixtures prove that the application connects end to end, not that its answers are
correct or that it supports a particular concurrency level.
