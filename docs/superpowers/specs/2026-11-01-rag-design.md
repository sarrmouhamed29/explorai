# Sub-project 3: RAG, design spec

Part of the master plan (`plan.md`). Date: 2026-11-01. Status: approved, then implemented on `feature/rag`; section 14 lists what the plan and the reviews changed.

## 1. Goal

Let a user ask a question and get an answer built from documents they are allowed to read (their own and the public corpus), where **every citation is verifiable** and the assistant **refuses to answer when the corpus does not contain the information**. The success criteria of `plan.md` for this sub-project: the assistant answers with verifiable sources and refuses when the corpus lacks the information. User isolation must hold all the way down to retrieval.

## 2. Scope

**In scope**
- Chunking of extracted sections, a `chunks` table, and an index of chunks in Qdrant (dense and sparse vectors).
- Hybrid retrieval (semantic plus keyword, fused by reciprocal rank fusion), reranking, optional query rewriting.
- Generation of a cited answer with Claude, code-side verification of citations, three levels of refusal.
- `POST /api/v1/ask`, an `indexed` flag on documents, per-user rate limit.
- Two Prefect deployments: `index-pending` and `prune-index`.
- A `qdrant` service in Compose; fakes for all external models so tests, CI and the end-to-end smoke test need no network or API key.
- A small hand-annotated question set and an on-demand quality measurement.

**Out of scope**
- Full evaluation with Ragas and Langfuse tracing, CI regression gates on quality: sub-project 4.
- Prompt-injection detection, PII filters, an adversarial test suite: sub-project 5 (this sub-project only applies structural mitigations).
- Conversation history, streaming answers, frontend pages: sub-project 6.
- Agents and tool use: sub-project 8.
- Comparison of chunking, embedding and reranker strategies, fine-tuning, model routing, caching: sub-project 11.
- Local (Ollama) models. They can be added behind the same interfaces.
- Production hardening of Qdrant (replication, snapshots, TLS): sub-project 10.

## 3. Decisions and rejected alternatives

| Topic | Decision | Alternatives not chosen |
|---|---|---|
| Models | Hosted APIs behind three interfaces (`Embedder`, `Reranker`, `Generator`) with fakes for tests. Claude (Anthropic API) generates and rewrites queries; Voyage AI provides embeddings and reranking (one key for both). Exact model names are pinned and checked against the providers' current documentation when the implementation plan is written; the vector dimension is a setting | Everything local with Ollama (free and private, but lower quality, slow without a GPU, heavy in CI and Compose); local embeddings plus hosted LLM (one paid provider fewer, but a heavy image and CPU load) |
| Search techniques | Everything the master plan lists: hybrid search, reranking, query rewriting, citations, refusal. Each optional stage has a setting so sub-project 4 can measure its gain | Hybrid plus citations only, adding the rest after measurement (rejected by the maintainer); semantic only (misses exact terms such as acronyms and model names, frequent in papers) |
| Vector store | Qdrant (dedicated vector engine) | pgvector in the existing PostgreSQL (would let the existing row-level security protect retrieval and removes a service; recommended, not chosen by the maintainer, who prefers the dedicated engine); local FAISS or Chroma (no access control) |
| Isolation in retrieval | One Qdrant collection per embedding model; `owner_id` payload index (tenant); a single function builds every filter; Qdrant returns only ids and the texts are re-read from PostgreSQL under `scoped_session`, so row-level security removes anything not visible before the model sees it | One collection per user (heavy, public corpus must be queried separately and merged); filter only (a single forgotten filter leaks, with no safety net) |
| Framework | Thin own code over the official SDKs | LlamaIndex (heavy abstractions, poor fit with owner-scoped retrieval); LangChain (unstable API, harder to fake; LangGraph may still be used for agents in sub-project 8) |
| Architecture | Chunks in PostgreSQL as source of truth, Qdrant as a rebuildable derived index, separate indexing stage | Qdrant as source of truth with text in payloads (loses the row-level security net); indexing inside the ingestion step (ingestion would depend on an external API) |

ADRs: Qdrant and its consequences for isolation; thin code instead of a framework; verified citations; providers behind interfaces.

## 4. Data model (migration 0005)

| Table | Content |
|---|---|
| `chunks` | `id`, `version_id` (cascade on delete), `ordinal`, `heading`, `text`, `word_count`, `text_hash`; unique `(version_id, ordinal)` |
| `chunk_index_entries` | `(chunk_id, index_name, indexed_at)`; unique `(chunk_id, index_name)`. `index_name` is the Qdrant collection name (embedding model and dimension). A chunk is pending when it has no entry for the current index |

Both tables follow row-level security like `sections`: visible when the document is public or owned by the current user, with the same restricted grants for `explorai_app`.

**What is indexed.** Only the latest `ok` version of each document, and only when it is canonical (`duplicate_of` is empty). Older versions and duplicates are not searchable: no duplicate results and no wasted embedding cost.

**Chunking** is a pure function over sections: windows of about 400 words with about 15% overlap, never crossing a section boundary, the section heading repeated at the start of each chunk for context. A cap on chunks per document bounds cost.

## 5. Indexing

**Qdrant collection.** Named after the embedding model and dimension, with an alias `chunks_current`. Changing the model creates a new collection that the pipeline indexes in the background without touching what the API serves, then switches the alias (its marker of the complete live collection); comparable to `extractor_version`. The API searches the collection of its own configured model (`RagSettings.index_name`), never the alias, so it never mixes models, and flips when its own configuration changes. Deploy order: re-index with the pipelines first, then change the backend's model settings. A point's id is the chunk id. Payload: `owner_id` (the value `"public"` for the public corpus), `document_id`, `version_id`; no text. Each point holds a dense vector (Voyage) and a sparse BM25-style vector computed locally (no API).

**Deployments** (each under its own PostgreSQL advisory lock, like `process-pending`):
- `index-pending`, every 30 seconds: chunks new eligible versions, embeds pending chunks in batches (rate limits, retries), upserts into Qdrant, records the index entry. A provider outage leaves chunks pending for the next pass; ingestion never depends on it.
- `prune-index`, hourly: removes points whose chunk no longer exists or is no longer in the latest canonical version. Deleting a document in PostgreSQL does not touch Qdrant; an orphan point is harmless because texts are re-read under row-level security.

## 6. Retrieval

All retrieval goes through one module, `Retriever`, the only code that talks to Qdrant. Stages, each switchable by a setting:

1. **Query rewriting (optional).** Claude returns up to 3 standalone search queries; the original question is always searched too. On failure or timeout, the original question alone is used.
2. **Hybrid search per query.** One Qdrant query with two prefetches (50 dense and 50 sparse candidates) fused by RRF, keeping 30. The filter is always `owner_id` equal to the current user or `"public"`, built by one function; an optional parameter narrows to one document or to "mine" or "public". Results of several queries are fused by a second RRF.
3. **Re-read under row-level security.** Ids are fetched from PostgreSQL through `scoped_session(user)`. Anything invisible disappears. Chunks that are no longer in the latest canonical version are dropped (stale index guard). Text never comes from Qdrant.
4. **Reranking.** Voyage reranks the 30 candidates; the best 8 are kept (settings). The best score drives the refusal threshold.

**Degradation.** Reranker down: keep RRF order and log it. Rewriting down: original question only. Query embedding impossible or Qdrant unavailable: `503`.

## 7. Generation

**Prompt.** A system message sets the rules: answer only from the sources, cite every claim, say so when the sources are not enough. The kept chunks are numbered `[1]` to `[8]` inside `<source>` tags with document title and section. The message states that source content is data, never instructions, and strings resembling our tags are neutralised inside chunk text.

**Structured output.** Claude is forced to call a tool `answer_question` whose schema requires `status` (`answered` or `insufficient`), `answer` (text with `[n]` markers) and `citations` (a list of `{source: n, quote: "exact text from the chunk"}`). No other tool is given: the model can execute nothing.

**Citation verification, done by our code.** Each `quote` must appear verbatim (after whitespace and case normalisation) in the cited chunk. A failing citation is removed. If none survives while the status was `answered`, the answer becomes a refusal.

**Three refusal levels**, all with HTTP `200` and an explicit `status` field:
1. before generation, when the best rerank score is below the threshold;
2. the model answers `insufficient`;
3. no verified citation remains.

**Parameters.** Temperature 0, capped output tokens, timeouts; models and thresholds are environment settings validated at startup. Neither the question nor document text is written to logs: only ids, counts and durations.

## 8. API

| Route | Purpose | Answers |
|---|---|---|
| `POST /api/v1/ask` | JSON `{question, scope: all\|mine\|public, document_id?}` | `200` with `status` (`answered` or `insufficient_evidence`), the answer and citations (document id and title, section, chunk, exact quote) and token usage; `422` empty or too long question (2000 characters); `429` hourly limit; `503` when Qdrant, Voyage or PostgreSQL is unavailable |
| `GET /api/v1/documents/{id}` | existing | gains a field `indexed`: true when the document's chunks are in Qdrant |

A `document_id` that is not visible to the user is `404`. Uploads still answer `202`; indexing follows, and `indexed` shows the state. Rate limit: 30 questions per hour per user (Redis, atomic reservation); a question that fails on the provider side gives its reservation back.

## 9. Configuration and infrastructure

New settings, validated at startup, secrets never in errors or logs: `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, model names, vector dimension, Qdrant URL and API key, batch sizes, candidate counts (50, 30, 8), refusal threshold, timeouts, switches for rewriting and reranking, `RAG_PROVIDER` (`real` or `fake`).

Compose adds a `qdrant` service: image pinned to an exact tag, port bound to `127.0.0.1`, named volume, API key enabled, health check. In CI and in the smoke test the providers are the deterministic fakes (`RAG_PROVIDER=fake`): no network and no key. Real keys are never in the repository or in CI.

## 10. Testing strategy

- **Unit (pure):** chunking (section boundaries, overlap, heading, empty or tiny text), citation verification, prompt building and tag neutralisation, RRF fusion, threshold decision, filter construction.
- **Fakes:** deterministic `FakeEmbedder`, `FakeReranker`, `FakeGenerator` behind the three interfaces, used by integration tests, CI and the smoke test.
- **Integration** (real PostgreSQL and Qdrant, fake models):
  - **Search isolation test, the success criterion.** Users A and B own private chunks, plus a public corpus. For any question from A, no chunk of B appears in the candidates, in the prompt sent to the fake generator, or in the answer. A variant deliberately removes the Qdrant filter and checks that the re-read under row-level security still stops B's chunks before the prompt.
  - Full path: upload, `index-pending`, `indexed`, question, answer with a verified citation.
  - A corpus lacking the answer: refusal, with no generator call when the threshold is not reached.
  - Document deletion: the orphan point never yields text; `prune-index` removes it.
  - Embedding model change: new collection indexed in the background while the API keeps serving its own model's collection; the alias moves once it is complete, and the API flips when its own configuration changes (pipelines first, then backend).
- **Smoke:** sign-up, upload, indexing, question and cited answer against the Compose stack with the fakes.
- Coverage gate stays at 80% per package.

**Measured quality.** A small hand-annotated set of questions over the sub-project 1 sample papers (about ten questions with the expected quote, plus a few with no answer in the corpus) gives a first measurement: share of questions whose expected source is among the 8 kept chunks, and share of correct refusals. It runs on demand with the real providers (a few cents) and its results go in `docs/evaluation/`. It is not in CI. Sub-project 4 replaces it with a full evaluation.

## 11. Threats and guardrails

| Threat | Guardrail |
|---|---|
| Leak between users through search | one filter-building function; texts re-read under row-level security; a test that removes the filter and checks nothing passes |
| Prompt injection in a document | sources tagged as data, no tool given to the model, citations verified verbatim; full defence in sub-project 5 |
| Private text sent to third parties | only the chunks selected for a question go to Anthropic and Voyage: a limitation to document for users; providers to review if sensitive data is targeted |
| Cost abuse (looping questions, huge uploads indexed) | hourly limit, 2000-character question, existing upload quotas, cap on chunks per document |
| API keys stolen or displayed | environment variables, never logged, `gitleaks` in hook and CI |
| Stale or poisoned index | Qdrant holds no text, is not authoritative, and is rebuilt from PostgreSQL |
| Provider outage | short timeouts, the degradations of section 6, explicit `503` |

## 12. Evaluation criteria (definition of done)

1. A user uploads a PDF, waits until `indexed` is true, asks a question and gets an answer whose every citation is verified verbatim in its source.
2. The search isolation test passes, including the variant without the filter.
3. A question with no answer in the corpus gives `insufficient_evidence`, and one case is shown without any generator call.
4. Each optional stage (rewriting, reranking) can be disabled by a setting, and each provider failure behaves as described.
5. The quality measurement on the annotated questions is recorded and reviewed by the maintainer before being quoted.
6. CI is green, including the end-to-end smoke test with the fakes, and the documentation exists: the ADRs above, a concepts note with interview questions and answers, updated READMEs.

## 13. Dependencies and next step

Depends on sub-projects 1 and 2. After spec approval, write the implementation plan; no code or dependency is added before the plan is approved. Sub-project 4 (Evaluation) follows and will measure each stage of this pipeline.

## 14. Refinements during planning

The implementation plan (`docs/superpowers/plans/2026-11-01-rag.md`) and the review rounds of its tasks settled or changed the points below. Sections 4 to 11 above are kept as approved, except where they were already corrected (the `index_name` key of section 4); this section is what was built. The reasons are in ADRs [0011](../../adr/0011-qdrant-and-retrieval-isolation.md) to [0014](../../adr/0014-providers-behind-interfaces.md) and in [`docs/concepts/03-rag.md`](../../concepts/03-rag.md).

**Data and indexing**

1. **`chunk_index_entries` is keyed by `index_name`**, the Qdrant collection name of the embedding model *and* dimension (`chunks_<model>_<dimension>`, `RagSettings.index_name`, at most 128 characters), not by the model alone. Keyed by the model, a dimension change, or a lost collection recreated empty, would have switched the alias to an empty collection that no pass refills. If the collection of the current index is missing while entries for it exist, `index-pending` clears those entries and rebuilds it from PostgreSQL.
2. **`document_versions.chunked_at`** marks a version as chunked, even into zero chunks, so it is not chunked again on every pass. Chunk rows of older versions are kept (they go with their version by cascade); only Qdrant points are pruned.
3. **Chunking** uses 400-word windows with 60 words of overlap (15%), skips sections under 5 words and stops at 500 chunks per document. The stored chunk text is the body only: the heading is prefixed to the text given to the encoders (`embedding_text`), not repeated in the stored text (section 4 said "repeated at the start of each chunk").
4. **One eligibility rule.** "The latest `ok` version, canonical" is one SQL expression, `explorai_ai.store.is_eligible_version` (ties on `created_at` broken by the larger id), used by both the API's `ChunkStore` and the pipeline's repository; an integration test checks that both select the same chunks. A duplicate document counts as `indexed` only when its canonical version is still the latest canonical `ok` version of its own document. Migration 0005 adds the index `ix_document_versions_document_status_created` for that subquery.
5. **Sparse vectors are computed by our own code** (Unicode word tokens with accents folded, `blake2b` 32-bit hashes, BM25-style saturating term frequency with `k1 = 1.2`, Qdrant's IDF modifier at query time) instead of a model downloaded on first use, so tests, CI and containers need nothing.
6. **Indexing deployments** run under advisory locks `INDEX_LOCK = 4242002` and `PRUNE_LOCK = 4242003` (the catch-up key stays `4242001`), every 30 s and every 3600 s; `serve` registers them only when `RAG_PROVIDER` is set (otherwise a warning, and the ingestion deployments keep working). A provider error stops a pass cleanly (`embedding_failed` counter); the alias moves only after a pass that leaves nothing pending. `prune-index` lists point ids before eligible ids, so it never deletes a point indexed meanwhile.

**Qdrant and retrieval**

7. **Image `qdrant/qdrant:v1.20.0`**, port bound to `127.0.0.1`, API key required, health check by `bash` opening a TCP connection (the image has no `curl` or `wget`). Alias `chunks_current`; the public corpus payload value is `"public"`.
8. **Qdrant errors:** a 404 on the API's collection (nothing indexed for its model yet) is an empty result, so the answer is a refusal, not a `503`; every other error response (5xx, but also non-404 4xx, 401/403, 429) and an unreachable server raise `RetrievalUnavailable` (`503`). The Qdrant client timeout is the provider timeout rounded up to whole seconds (a sub-second setting never becomes 0).
9. **The retriever calls the filter builder through its module** (`scoping.build_scope_filter`) so the isolation test can replace it; the AST test that forbids building filters elsewhere exempts `scope.py` and `index.py` by file name.
10. **The refusal threshold applies only after a successful rerank.** With reranking disabled or failed, `best_score` is `None` (RRF scores depend only on ranks and cannot be compared with a relevance threshold) and the generator decides. A reranker returning no usable hit counts as a failure.

**Generation and citations**

11. **Providers:** Voyage over REST with `httpx` (testable with `MockTransport`), Anthropic through its official SDK with an injectable client and SDK retries off (`retry_transient` is the only retry layer). Defaults `voyage-4` at 1024 dimensions and `rerank-2.5` (read through a summarising fetch tool: to re-verify before the first real call), `claude-sonnet-5-5` and `claude-haiku-5-5`.
12. **Prompt bounds:** each source is capped at 8000 characters (above the roughly 3000 of a normal 400-word chunk) and the question at 2000; quotes are still verified against the full stored chunk. Titles and headings lose quotes, `=`, angle brackets and control characters so they cannot escape their attribute. Answers are capped at 4096 output tokens. The stop reasons `max_tokens` and `model_context_window_exceeded` are errors; `refusal` (the model declines) is an `insufficient` answer, hence `insufficient_evidence`, not a `503`.
13. **Citation floor: at least 2 words and 10 characters** after normalisation (NFKC, case folding, whitespace collapsed, surrounding quotation marks and ellipses stripped). The first ruling (3 characters) let "the" verify against almost any chunk. The quote returned is the model's text matched after normalisation, not the source's exact span.
14. **Errors to `503`:** transient generation errors propagate as `ProviderUnavailable`; other generation errors are wrapped in `GenerationFailed` (a `ProviderUnavailable`), so the route maps exactly `ProviderUnavailable` and `RetrievalUnavailable` to `503`. Database connection errors and pool timeouts answer `503` through the existing handlers.

**API and configuration**

15. **The reservation is given back on every outcome that is not served** (over the limit, a `document_id` that is not visible (`404`), a provider, Qdrant or database outage, a bug), not only on provider failures; an answer or a refusal keeps it. The give-back is an optimistic `WATCH`/`MULTI` decrement, dropped under a concurrent change (strict side).
16. **Environment names:** the library's own settings use the `RAG_` prefix (`RAG_PROVIDER`, `RAG_ASK_MAX_PER_HOUR`, ...); `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `QDRANT_URL` and `QDRANT_API_KEY` keep their usual names. Blank keys count as missing. An invalid configuration stops the backend at startup with a message naming the environment variables (`RAG_PROVIDER`), never their values. `RAG_QUESTION_MAX_CHARS` exists but the route enforces a fixed 2000.
17. **Packaging and CI:** `ai/` is a fourth workspace member, flat (`explorai_ai.retriever`, no `rag/` subpackage yet); both Dockerfiles copy it; the backend has a dev-only dependency on `pipelines` for the RAG integration tests; the `ai` ruff, mypy and pytest steps and the Qdrant service container run in the CI `backend` job.
18. **Measurement:** `explorai_ai.measure` wraps the retriever to record the kept chunks instead of changing the pipeline, asks on the `public` scope with a placeholder user, and also reports false refusals. The 14 questions are unvalidated by the maintainer and no real run exists (`docs/evaluation/rag-baseline.md`).

**Known channel**

19. **Shared sparse IDF is a confirmed, low-bandwidth cross-tenant term-membership channel.** Qdrant's `Modifier.IDF` computes document frequencies over the whole collection regardless of the owner filter. No text leaks, but a user who uploads one document of more than 30 near-identical chunks (one with a probe term X, others with a term Y whose frequency they control) and asks "X Y" scoped to it can infer from the rank or survival of the X chunk whether another tenant or the public corpus contains X: about one bit per few of the 30 questions per hour, more directly with reranking disabled. Accepted and documented; mitigation planned for the safety sub-project: per-tenant IDF computed client-side, no IDF, or per-tenant collections or shard keys (ADR 0011).

**Verification status.** Docker was unresponsive during the whole implementation: none of the integration suites (`db`, `ai`, `pipelines`, `backend`, including the search-isolation suite and its mutation proof), the smoke test, the image builds or the Qdrant health check has run. CI is their first run, and nothing merges before it is green. The real providers have never been called.
