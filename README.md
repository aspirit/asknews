# asknews

Bilingual question answering over the public communications of central banks.

The archive covers what the Federal Reserve publishes in English and what the
Bank of Japan publishes in Japanese: press conference videos and transcripts,
policy statements, meeting minutes, speeches and outlook reports. Ask a question
in either language and get an answer built only from retrieved passages, each
one cited back to the official document or to a moment in the video.

## Principles

- **Sources first.** Retrieval results are shown before the generated answer,
  and the answer may only use what was retrieved.
- **Declines out of scope.** Questions unrelated to the archive are refused
  rather than answered from general knowledge.
- **Hybrid retrieval.** Lexical search handles rare terms and exact phrases;
  dense retrieval handles paraphrase and cross-language questions.
- **No re-hosting of video.** Official players are embedded and transcripts
  link to timestamps where the source provides them.

## Status

Early development. The repository is being built up in small, reviewable
steps; see the commit history for what exists so far.

## Development

Requirements: [uv](https://docs.astral.sh/uv/) and Python 3.13 (uv installs
it on demand).

```sh
uv sync --all-groups
make check
```

## License

MIT. Source documents remain under the terms of their publishers; those terms
are documented alongside each ingestion module.
