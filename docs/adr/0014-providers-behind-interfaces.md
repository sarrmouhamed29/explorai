# 0014. Model providers behind interfaces, with fakes for every test

- Status: Accepted
- Date: 2026-11-01

## Context

The RAG pipeline needs three model capabilities: embeddings (dense vectors for chunks and questions), reranking (scoring candidates against the question) and generation (rewriting the question into search queries, and writing the cited answer). Each can be a hosted API or a local model. Tests, CI and the end-to-end smoke test must run without network access and without any API key, and the answers they check must be reproducible.

## Decision

**Hosted providers behind three ports.** `explorai_ai.providers.base` defines three `typing.Protocol`s, the only things the pipeline depends on:

- `Embedder`: `model`, `dimension`, `embed_documents(texts)`, `embed_query(text)`;
- `Reranker`: `rerank(query, documents, top_k)`, hits sorted by score;
- `Generator`: `rewrite_queries(question, max_queries)`, `answer(question, sources)`.

**Adapters** (the real implementations):

- `VoyageEmbedder` and `VoyageReranker` (one Voyage AI key for both) call Voyage's REST API with `httpx`: `POST /v1/embeddings` (`input_type` `document` or `query`, `output_dimension`) and `POST /v1/rerank`. They batch, restore the order by `index`, check vector lengths and reject malformed numbers (NaN, infinity, strings, duplicates).
- `AnthropicGenerator` uses the official `anthropic` SDK with an injected client. `answer` **forces a tool call** (`tool_choice` on `answer_question`, the only tool given), at temperature 0 with output capped at 4096 tokens (room for a long answer and its quotes: a cut-off answer would fail again on every retry); the tool input is validated strictly, stop reasons `max_tokens` and `model_context_window_exceeded` are errors (`503`), and a `refusal` stop (the model declines) is an explicit `insufficient` answer, so the API returns `insufficient_evidence` rather than a `503`. `rewrite_queries` asks the cheaper model for a JSON list and returns `[]` on any bad output.
- Defaults (all settings): `voyage-4` at 1024 dimensions, `rerank-2.5`, `claude-sonnet-5-5` for answers, `claude-haiku-5-5` for rewriting.

**Fakes** (`explorai_ai.fakes`): `FakeEmbedder` (word tokens hashed into a normalised vector, so texts sharing words are close), `FakeReranker` (share of question words found in the document) and `FakeGenerator` (picks the source with the largest word overlap, refuses below 30%, otherwise quotes that source's first sentence verbatim). Deterministic, no I/O.

**One switch.** `RAG_PROVIDER=fake|real` (required, no default); `factory.build_providers(settings)` returns the fakes or the adapters. `real` refuses to start without non-blank `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY`.

**Our own error vocabulary.** Vendor failures are mapped at the adapter boundary to `ProviderUnavailable` (timeouts, connection errors, 5xx), `ProviderRateLimited` (429, a subclass of it) and `ProviderError` (other 4xx, malformed responses). Messages carry status codes only, never a response body or a key; keys are `SecretStr` and absent from `repr`. `retry.retry_transient` retries only `ProviderUnavailable` (3 attempts, delays 0.5 s then 2 s); the SDK's own retries are off (`max_retries=0`) so there is one retry layer, not two multiplied together. The pipeline turns permanent generation errors into `GenerationFailed` (a `ProviderUnavailable`), so the API maps exactly two exception families to `503`.

## Alternatives considered

- **Everything local with Ollama.** Free and private, but lower quality, slow without a GPU, and heavy in CI and Compose. The ports allow adding it later as another adapter.
- **Local embeddings (sentence-transformers) with a hosted LLM.** One paid provider fewer, but a large image, CPU load on every indexing pass, and a model download in CI.
- **Mocks instead of fakes** (`unittest.mock` objects returning canned values per test). Mocks verify calls; fakes have behaviour. A fake embedder that puts related texts close together lets an integration test check real retrieval, fusion, refusal and citation logic end to end, which a canned return value cannot.
- **Voyage's Python SDK.** Possible, but REST over `httpx` is a few lines, adds no dependency, and is testable with `httpx.MockTransport` without patching.
- **OpenAI or another single vendor for both embeddings and generation.** Anthropic offers no embedding or rerank API, so generation with Claude needs a second vendor anyway; Voyage gives both embeddings and reranking under one key. No quality comparison of vendors was made by us; sub-project 11 compares models.

## Consequences

- Every test in the repository runs with the fakes: no network, no key, reproducible. The adapters are tested only against `httpx.MockTransport` and a stub Anthropic client.
- **The real providers have never been called.** No agent had keys, and none may hold one. The first real call (the quality measurement of `docs/evaluation/rag-baseline.md`) is the maintainer's.
- **Voyage facts must be re-verified before that first call.** The endpoint shapes, `input_type` values, batch limit (1000 texts), `output_dimension` values and the model names `voyage-4` and `rerank-2.5` were read through a fetch tool that summarises pages with a small model, not read verbatim. Check them on <https://docs.voyageai.com/reference/embeddings-api> and <https://docs.voyageai.com/reference/reranker-api>. The Anthropic model ids were checked against the installed SDK's `Model` type (anthropic 2.3.0), not against a live call.
- **Private text leaves our infrastructure.** This is a limitation users must be told about:
  - at **indexing**, the text of **every chunk of every indexed document**, private uploads included, is sent to Voyage to be embedded;
  - at **question time**, the question is sent to Anthropic (rewriting, when enabled) and to Voyage (embedding of the question and of up to 3 rewrites), the up to 30 hydrated candidate chunks are sent to Voyage with the question (reranking), and the up to 8 kept chunks are sent to Anthropic with the question (generation).
  The providers' data retention and training terms have not been reviewed in this sub-project. Before any sensitive data is targeted, review them, or add a local adapter behind the same ports.
- **The fakes are not models.** They match words, so they say nothing about quality; numbers produced with them (the `--provider fake` run of `measure.py`) must never be quoted as results.
- **Failure behaviour is a contract.** Rewriting down: the question alone is searched. Reranking down: the fused order is kept and the refusal threshold is not applied. Query embedding, Qdrant or generation down: `503`. These degradations are unit-tested with fakes that fail on demand.
- Known gaps from the reviews: HTTP 408 from Voyage is treated as a permanent `ProviderError` although it is transient; a JSON integer too large for a float in a Voyage response escapes as a raw `OverflowError`; the Voyage `truncation` parameter is left at the provider's default; `rewrite_queries` does not check the stop reason (bad output already degrades to `[]`).
