# RAG: deferred findings

Minor findings from the per-task and final reviews of sub-project 3 that were deferred on purpose. None blocks the merge; each is a candidate for a later change. Findings that were fixed or documented during the branch are not listed. They are recorded here because the review workspace is deleted when the branch is finished.

**Nothing in this list has been run against a live stack.** Docker was unresponsive for the whole sub-project, so the integration suites, the smoke test, the migration round trip and the mutation proofs of the isolation tests have never run. CI is their first execution.

## Security and robustness

- Sparse IDF (`Modifier.IDF`) is a confirmed low-bandwidth cross-tenant term-membership channel (ADR 0011). The real fix (per-tenant IDF, no IDF, per-tenant collections or shard keys) belongs to the safety sub-project.
- No overall deadline per `/ask`: during a provider brown-out one request can hold a threadpool slot for minutes (rewrite 3 tries, up to 4 embeddings, rerank, generation).
- The hourly limiter's `release` is dropped on a Redis `WatchError` under concurrent requests (errs on the strict side); a Lua "decrement if positive" script would be atomic.
- `math.isfinite` raises a raw `OverflowError` for an astronomically large JSON integer in embeddings or rerank scores; the retriever has no `isfinite` guard on rerank scores.
- 401/403/429 from Qdrant during indexing and ensure/upsert are re-raised raw; Qdrant errors raise out of an indexing pass.
- Control characters and newlines in titles are not collapsed in the prompt, look-alike brackets are not covered, and `neutralise` also rewrites `<sourced`.
- 408/409 from a provider map to a non-retried error (408 is transient); `rewrite_queries` does not check `stop_reason`.
- A permanent `ProviderError` (bad key) looks like an outage and leaves the indexing flow green.
- Invalid RAG settings also stop the ingestion flows, because `build_runtime` is shared (add a README line or split the settings check).
- The Qdrant health check in Compose and CI proves only that the TCP port is open (`/readyz` through `/dev/tcp` plus a raw HTTP GET would be stricter).

## Indexing

- A document deleted mid-pass makes `record_entries` hit a foreign-key violation (the insert is `SELECT id FROM chunks`).
- One deterministic failure in `chunk_pending_versions` blocks all indexing; there is no per-pass cap, so a full re-embed holds the advisory lock for hours.
- Eligible ids and point listings are held in memory during prune.
- Old collections are never deleted after an alias switch (documented, manual).
- `collection_exists` then `create_collection` is racy under concurrent callers (indexing runs under an advisory lock); the 404 warning does not distinguish a wrong base URL from a missing alias.
- Upsert sends all points of a batch in one request; no HNSW tuning for the tenant index.
- `settings` is unused in `prune_index`; no test pins the prune ordering.

## Retrieval and answers

- The quote shown to the user is the model's text, matched case/NFKC-insensitively, not the source's exact span.
- Dropping `Mn` marks over-folds some scripts (Japanese dakuten, Thai and Indic marks); CJK runs become whole-run tokens; NFKD data depends on the Python `unicodedata` version. Quotes in languages without spaces never pass the 2-word floor.
- A bibliographic `[3]` copied inside quoted text is ambiguous with a citation marker.
- `usage` counts only the generation call; pre-generation refusals report 0 although providers were billed.
- `scope="mine"` or `"public"` with a visible `document_id` outside that scope refuses instead of answering `422`.
- `RagSettings.question_max_chars` is unused by the route (fixed 2000).
- No store test for a cross-owner `duplicate_of` (argued: RLS plus the explicit owner check).

## Tests and tooling

- The AST filter scan misses `from ... import Filter as F` aliases and plain dicts, matches basenames, and passes silently if a directory is missing.
- Tests: an engine fixture is not disposed in `test_chunks_schema.py` and the new index test; the query counter in `test_store.py` is fragile; the candidate-set equalities in the isolation suite assume Qdrant returns every visible point of a tiny collection; `test_ask_api.py` has redundant imports; `ai` tests import `explorai_pipelines` without declaring it.
- The measurement CLI writes `RAG_PROVIDER` into `os.environ`; `load_questions` does not validate types, so `score_retrieval("")` is vacuously true; `score_retrieval` does not strip edge quote marks or ellipses; a Qdrant connection error in `_build_real` gives a traceback instead of exit 2; `MeasuredPipeline` is not re-entrant.
- The 14 annotated questions are not validated by the maintainer, and no real-provider measurement exists.
- `infra/docker-compose.yml` has CRLF line endings in the working copy (the committed blob is LF); `QDRANT_API_KEY` with `:?` makes every Compose command fail on an `infra/.env` without it.
- The Voyage model names and limits came from a summarising fetch and must be re-verified before the first real call.
