# Design decisions

These decisions describe the current implementation and its limits. They are not
claims about traffic levels, measured capacity or the accuracy of generated text.

## 1. Keep relational data and retrieval indexes in PostgreSQL

**Context.** A passage belongs to a document, publisher, language and publication
date. Retrieval needs those constraints alongside full text, phrase matching and
vector similarity. Document updates must remove obsolete passage vectors.

**Decision.** Use PostgreSQL with full-text indexes, pg_trgm and pgvector. Foreign
keys give passages and vectors the same deletion lifecycle. The ingest store
preserves vectors on a no-op import and updates timing corrections without
re-embedding unchanged text.

**Alternative.** A separate search engine or vector service could scale indexing
and queries independently, but introduces another write path, synchronisation
work and failure modes. There is no measured need for that separation yet.

**Cost and boundary.** Ingest and public queries share database resources. A
single database is not a guarantee of fast queries, especially with selective
filters over an approximate vector index. Measure plans, buffer reads and tail
latency with representative data before changing the storage architecture.

**Evidence.** `tests/ingest/test_store.py` covers text replacement, vector cleanup,
timing-only updates and rollback. `tests/retrieval/test_retrieve.py` exercises
retrieval against PostgreSQL rather than mocking SQL results. Summary and brief
refresh are separate from segment replacement; these tests do not claim automatic
regeneration of every derived document after a source revision.

## 2. Combine lexical and dense retrieval before generation

**Context.** Names, dates and exact policy terms benefit from lexical matches;
paraphrases and cross-language questions need semantic retrieval. Neither path
alone covers both well by construction.

**Decision.** Fuse candidates with reciprocal-rank scores, then apply the existing
publisher, speaker, date and document-kind signals. Limit passages per document
and attach the answer following a retrieved reporter question where available.
Only then send numbered passages to the answer model.

**Alternative.** Dense-only retrieval reduces matching rules but can miss exact
terms. Lexical-only retrieval avoids query embedding cost but cannot establish
cross-language meaning. Passing whole documents to the generator spends tokens
without first identifying the supporting evidence.

**Cost and boundary.** More paths mean more query work and ranking parameters.
There must be evidence that fusion improves the desired tasks. `asknews eval`
provides application-level lexical/dense/hybrid comparisons with passage checks;
the fixed demo verifies execution, not this quality hypothesis. Citation checks
validate references, not whether a sentence is entailed by its source.

**Revisit when.** A held-out evaluation shows weak recall, wrong speakers or poor
cross-language results. Change the failing stage and rerun the same corpus and
cases before adding a reranker or replacing the embedding model.

## 3. Reserve spending for public Ask requests

**Context.** Public questions arrive concurrently and cannot be scheduled by the
operator. Background summaries and briefs are operator-controlled jobs.

**Decision.** Public Ask atomically checks and reserves its estimated budget and
client allowance under a short database advisory lock. It releases that lock
before retrieval and the network call, then finalises the request with the result
and estimated actual usage. Background generation uses its separate admission
check and generation share; it does not take an Ask reservation.

**Alternative.** Reserving every background call would impose a shared scheduler
on controlled maintenance work. It is not required for the current operating
model and would add coordination beyond the public-request boundary.

**Cost and boundary.** This is not a transactional cap across arbitrary concurrent
generation workers and public answers. Recorded costs are estimates, not provider
invoices. If background work becomes user-triggered or many autonomous workers
are introduced, revisit reservations for those callers explicitly.

**Evidence.** `tests/answer/test_pipeline.py` covers concurrent public budget and
rate admission, failure cleanup and truncated replies. Those guarantees should
not be described as applying to uncontrolled background generation.

## Validation boundaries

- [Walkthrough](walkthrough.md): reproducible local execution with synthetic text.
- [Evaluation](evaluation.md): evidence-level retrieval checks and their metrics.
- `make check`: lint, typing and tests; database tests require `TEST_DATABASE_URL`.
- Load capacity and production answer quality need separate, representative runs.
  No throughput or answer-quality score is inferred from the fixture suite.
