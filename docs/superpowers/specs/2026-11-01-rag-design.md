# Sub-project 3: RAG, design spec

Part of the master plan (`plan.md`). Date: 2026-11-01. Status: draft, awaiting review.

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
| `chunk_index_entries` | `(chunk_id, embedding_model, indexed_at)`; unique `(chunk_id, embedding_model)`. A chunk is pending when it has no entry for the current model |

Both tables follow row-level security like `sections`: visible when the document is public or owned by the current user, with the same restricted grants for `explorai_app`.

**What is indexed.** Only the latest `ok` version of each document, and only when it is canonical (`duplicate_of` is empty). Older versions and duplicates are not searchable: no duplicate results and no wasted embedding cost.

**Chunking** is a pure function over sections: windows of about 400 words with about 15% overlap, never crossing a section boundary, the section heading repeated at the start of each chunk for context. A cap on chunks per document bounds cost.

## 5. Indexing

**Qdrant collection.** Named after the embedding model and dimension, with an alias `chunks_current`. Changing the model creates a new collection, indexes it in the background, then switches the alias: no downtime, comparable to `extractor_version`. A point's id is the chunk id. Payload: `owner_id` (the value `"public"` for the public corpus), `document_id`, `version_id`; no text. Each point holds a dense vector (Voyage) and a sparse BM25-style vector computed locally (no API).

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
  - Embedding model change: new collection indexed, alias switched without a gap.
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
