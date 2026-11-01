# explorai-ai

The AI library of Explorai (importable as `explorai_ai`). It never depends on `backend/`.

- `config`: `RagSettings`, read from the environment (`RAG_PROVIDER=real|fake`).
- `providers.base`: the ports (`Embedder`, `Reranker`, `Generator`) the RAG logic depends on.
- `fakes`: deterministic in-memory implementations used by every test (no network, no API key).
- `retry`: `retry_transient`, which retries only transient provider errors.
