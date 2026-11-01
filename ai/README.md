# explorai-ai

The AI library of Explorai (importable as `explorai_ai`). It never depends on `backend/`.

- `config`: `RagSettings`, read from the environment (`RAG_PROVIDER=real|fake`).
- `providers.base`: the ports (`Embedder`, `Reranker`, `Generator`) the RAG logic depends on.
- `fakes`: deterministic in-memory implementations used by every test (no network, no API key).
- `retry`: `retry_transient`, which retries only transient provider errors.
- `store`: `ChunkStore`, which re-reads chunk texts from PostgreSQL under Row-Level Security (`hydrate`) and tells whether documents are fully indexed (`indexed_state`). It also holds `is_eligible_version`, the one definition of "the searchable version of a document": the latest `ok` version (ties on `created_at` broken by the larger id), only when canonical (`duplicate_of IS NULL`).

Integration tests (`-m integration`) need `DATABASE_URL` (a PostgreSQL superuser), like `db/`.
