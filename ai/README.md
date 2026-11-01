# explorai-ai

The AI library of Explorai (importable as `explorai_ai`). It never depends on `backend/`.

- `config`: `RagSettings`, read from the environment (`RAG_PROVIDER=real|fake`).
- `providers.base`: the ports (`Embedder`, `Reranker`, `Generator`) the RAG logic depends on.
- `fakes`: deterministic in-memory implementations used by every test (no network, no API key).
- `retry`: `retry_transient`, which retries only transient provider errors.
- `store`: `ChunkStore`, which re-reads chunk texts from PostgreSQL under Row-Level Security (`hydrate`) and tells whether documents are fully indexed (`indexed_state`). It also holds `is_eligible_version`, the one definition of "the searchable version of a document": the latest `ok` version (ties on `created_at` broken by the larger id), only when canonical (`duplicate_of IS NULL`).
- `index`: `QdrantIndex`, the Qdrant adapter. Named dense (cosine) and sparse (IDF) vectors, a tenant index on `owner_id`, hybrid search by two prefetches fused with RRF (the scope filter is passed to each prefetch and to the fusion stage), and an alias (`chunks_current`) switched atomically so a re-index goes live without downtime. A missing alias or collection (404) is an empty result; an unreachable store and any other error response (5xx, but also a 400 "vector dimension error" while the alias still serves the previous dimension's collection, 401/403, 429) raise `RetrievalUnavailable` with a fixed message and no chained response.
- `retriever`: `Retriever.retrieve`, which rewrites the query (optional, a failure searches the question alone), runs one hybrid search per query under `scope.build_scope_filter`, fuses the queries with `rrf_fuse` (`sum 1/(60 + rank)`, ties by id), re-reads the texts through `ChunkStore.hydrate` and reranks (optional, a failure keeps the fused order and reports no `best_score`). A query that cannot be embedded raises `RetrievalUnavailable`.
- `citations`: `verify_citations`, which keeps a citation only if its quote (NFKC, case-folded, whitespace collapsed, surrounding quotation marks and ellipses stripped) has at least 2 words and 10 characters and occurs in the chunk it cites.
- `pipeline`: `AnswerPipeline.ask`, which refuses (`insufficient_evidence`) without calling the generator when nothing is retrieved or the best rerank score is below `refusal_threshold`, and after generation when the model says `insufficient` or no citation survives verification. Provider failures escape only as `RetrievalUnavailable` or `ProviderUnavailable` (`GenerationFailed` wraps permanent generation errors), which the API maps to 503.

Integration tests (`-m integration`) need `DATABASE_URL` (a PostgreSQL superuser), like `db/`, and `QDRANT_URL` / `QDRANT_API_KEY` (the Compose `qdrant` service).

## Providers (real)

- `prompt`: `SYSTEM_PROMPT`, `build_user_message` and `neutralise`. Document text goes inside numbered `<source>` tags declared to be data; `neutralise` replaces the angle brackets of any `<source>`/`<question>` tag found in titles, headings, bodies or the question by look-alikes, so text cannot close or open our tags. `ANSWER_TOOL` is the schema of the forced `answer_question` tool.
- `providers.voyage`: `VoyageEmbedder` and `VoyageReranker` over Voyage's REST API with `httpx` (`/v1/embeddings` with `input_type` `document`/`query` and `output_dimension`; `/v1/rerank`). Batches, restores order by `index`, checks the vector length, retries through `retry_transient`. 429 maps to `ProviderRateLimited`, 5xx and transport errors to `ProviderUnavailable`, other 4xx to `ProviderError`; the key never appears in errors or `repr`.
- `providers.anthropic_generator`: `AnthropicGenerator` over the official SDK. `answer` forces the `answer_question` tool at temperature 0 and validates its input strictly; `rewrite_queries` asks the cheaper model for a JSON list and returns `[]` on any bad output. SDK retries are off (`max_retries=0`): `retry_transient` is the only retry layer.
- `factory.build_providers(settings)`: the fakes for `RAG_PROVIDER=fake`, the real adapters for `real` (clients are injectable for tests).

Defaults (checked against the providers' documentation on 2026-11-01): `voyage-4` at 1024 dimensions, `rerank-2.5`, `claude-sonnet-5-5` for answers, `claude-haiku-5-5` for query rewriting. Changing the embedding model or dimension requires a re-index (new Qdrant collection): `RagSettings.index_name` is the collection name, and index entries are keyed by it, so either change makes every chunk pending for the new index. A model name that would make the index name longer than 128 characters is refused at startup.
