# Retrieval evaluation

`asknews eval` checks retrieved evidence without generating answers or spending
answer tokens. A configured remote embedding service may still incur costs.
The five shipped cases are regression checks, not a benchmark of answer quality.

## Run on a fixed corpus

Use a local, immutable copy of the corpus. Keep the same database, model revision,
domain configuration and reference date across comparisons:

```sh
uv run asknews eval --mode lexical --as-of 2026-10-01 \
  --corpus-label local-snapshot-20261001 --report data/eval/lexical.json
uv run asknews eval --mode dense --as-of 2026-10-01 \
  --corpus-label local-snapshot-20261001 --report data/eval/dense.json
uv run asknews eval --mode hybrid --as-of 2026-10-01 \
  --corpus-label local-snapshot-20261001 --report data/eval/hybrid.json
```

The label is supplied by the operator, not a computed corpus fingerprint. The
report records corpus counts, embedding model counts, a hash of the cases file,
the reference date and each result. Each run uses a read-only repeatable-read
transaction; separate runs still need an unchanged corpus. `--as-of` fixes the
interpretation of relative dates, not which publications exist in the database.
Do not pass a production database just to reproduce an example.

Modes change candidate selection, not the rest of the pipeline. Dense mode still
uses lexical evidence for the common domain gate; both modes retain ranking boosts
and question/answer expansion. This is an ablation of the application, not a pure
nearest-neighbour benchmark. Lexical mode makes no embedding requests. The hash
embedder is a deterministic test stand-in and cannot measure cross-language quality.

## Annotate evidence, not just document names

```yaml
cases:
  - id: statement-evidence
    question: What did the Fed say about inflation in September 2025?
    expect:
      institution: fed
      kind: statement
      external_id: "20250917"
      passage_contains: Inflation has moved up
    within: 5
  - id: outside-archive
    question: How do I bake a birthday cake?
    outcome: out_of_scope
  - id: unavailable-period
    question: What did the Fed decide in 2001?
    outcome: no_sources
```

Negative cases must be appropriate for the chosen corpus. An unavailable period
is no longer a negative once that period is ingested. `out_of_scope` tests the
domain gate; `no_sources` requires an in-domain query with no citations. Neither
asserts that the generator can recognise irrelevant but nonempty retrieval.

All `expect` fields must match the same citation. `passage_contains` checks the
full passage, folding case and whitespace, not the truncated display snippet.
Use publisher identifiers rather than database ids. Unknown fields, empty positive
expectations, duplicate case ids and invalid cutoffs fail before retrieval.

## Read the report

- Hit@5 and MRR@12 use positive cases only; ranks are positions in the final
  citation list, not unique-document ranks. Missing evidence contributes zero.
- Negative accuracy is reported separately. An empty category is `null`, not 100%.
- Passing a positive case uses its own `within` cutoff, which can differ from 5.
- P50/P95 use nearest-rank percentiles of single serial retrieval calls, including
  embedding latency but excluding relevance lookups. They are not load-test results.
- Exit 0 means all cases passed, 1 means a failed case, 2 means invalid cases.
  A report is written even when a valid case fails.

Before claiming retrieval quality, curate at least 40 evidence-labelled questions
covering English, Japanese, cross-language questions, dates and refusals. Reserve
10 for held-out evaluation; do not tune against their answers. Separately review
10 generated answers claim by claim: supported, unsupported or not answerable.
Citation-number validity is not factual entailment. No production score is claimed
by the repository's fixture tests.
