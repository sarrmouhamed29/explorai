# explorai-ai

The AI library of Explorai (importable as `explorai_ai`): retrieval-augmented generation over the documents a user may read. It depends on `db/` (models and the RLS session) and never on `backend/` or `pipelines/` (ADR [0005](../docs/adr/0005-ai-library-independent-of-backend.md)); the API (`backend/`) and the indexing jobs (`pipelines/`) call it. The design is in `docs/superpowers/specs/2026-11-01-rag-design.md`, the concepts in [`docs/concepts/03-rag.md`](../docs/concepts/03-rag.md), the choices in ADRs [0011](../docs/adr/0011-qdrant-and-retrieval-isolation.md) to [0014](../docs/adr/0014-providers-behind-interfaces.md).

```
question -> rewrite (optional) -> per query: dense embed + local sparse encode -> Qdrant hybrid search
         under the owner filter (RRF) -> RRF across queries (30) -> re-read from PostgreSQL under RLS
         -> rerank (optional, 8) -> refuse below the threshold -> forced-tool answer -> verify every quote
```

## Modules

- `config`: `RagSettings`, read from the environment (`RAG_PROVIDER=real|fake`), see "Settings" below.
- `providers.base`: the ports (`Embedder`, `Reranker`, `Generator`) the RAG logic depends on.
- `fakes`: deterministic in-memory implementations used by every test (no network, no API key).
- `retry`: `retry_transient`, which retries only transient provider errors.
- `chunking`: `chunk_sections`, windows of `RAG_CHUNK_WORDS` (400) words with `RAG_CHUNK_OVERLAP_WORDS` (60) of overlap, never across a section, sections under 5 words skipped, at most `RAG_MAX_CHUNKS_PER_DOCUMENT` (500); `embedding_text` prefixes the heading for the encoders (the stored text is the body only).
- `sparse`: the local sparse encoder (Unicode word tokens with accents folded, `blake2b` 32-bit token hashes, BM25-style saturating term frequency with `k1 = 1.2`; Qdrant applies IDF).
- `scope`: `build_scope_filter`, the only function in the repository that builds a Qdrant filter (an AST test enforces it); the `owner_id` condition is always present.
- `store`: `ChunkStore`, which re-reads chunk texts from PostgreSQL under Row-Level Security (`hydrate`) and tells whether documents are fully indexed (`indexed_state`). It also holds `is_eligible_version`, the one definition of "the searchable version of a document": the latest `ok` version (ties on `created_at` broken by the larger id), only when canonical (`duplicate_of IS NULL`).
- `index`: `QdrantIndex`, the Qdrant adapter. Named dense (cosine) and sparse (IDF) vectors, a tenant index on `owner_id`, hybrid search by two prefetches fused with RRF (the scope filter is passed to each prefetch and to the fusion stage), and an alias (`chunks_current`) switched atomically so a re-index goes live without downtime. A missing alias or collection (404) is an empty result; an unreachable store and any other error response (5xx, but also a 400 "vector dimension error" while the alias still serves the previous dimension's collection, 401/403, 429) raise `RetrievalUnavailable` with a fixed message and no chained response.
- `retriever`: `Retriever.retrieve`, which rewrites the query (optional, a failure searches the question alone), runs one hybrid search per query under `scope.build_scope_filter`, fuses the queries with `rrf_fuse` (`sum 1/(60 + rank)`, ties by id), re-reads the texts through `ChunkStore.hydrate` and reranks (optional, a failure keeps the fused order and reports no `best_score`). A query that cannot be embedded raises `RetrievalUnavailable`.
- `citations`: `verify_citations`, which keeps a citation only if its quote (NFKC, case-folded, whitespace collapsed, surrounding quotation marks and ellipses stripped) has at least 2 words and 10 characters and occurs in the chunk it cites (ADR 0013).
- `pipeline`: `AnswerPipeline.ask`, which refuses (`insufficient_evidence`) without calling the generator when nothing is retrieved or the best rerank score is below `refusal_threshold`, and after generation when the model says `insufficient` or no citation survives verification. Provider failures escape only as `RetrievalUnavailable` or `ProviderUnavailable` (`GenerationFailed` wraps permanent generation errors), which the API maps to 503.
- `measure`: the on-demand quality measurement over the annotated questions (see below).

## Providers (real)

- `prompt`: `SYSTEM_PROMPT`, `build_user_message` and `neutralise`. Document text goes inside numbered `<source>` tags declared to be data; `neutralise` replaces the angle brackets of any `<source>`/`<question>` tag found in titles, headings, bodies or the question by look-alikes, so text cannot close or open our tags. Each source is capped at 8000 characters and the question at 2000. `ANSWER_TOOL` is the schema of the forced `answer_question` tool.
- `providers.voyage`: `VoyageEmbedder` and `VoyageReranker` over Voyage's REST API with `httpx` (`/v1/embeddings` with `input_type` `document`/`query` and `output_dimension`; `/v1/rerank`). Batches, restores order by `index`, checks the vector length, retries through `retry_transient`. 429 maps to `ProviderRateLimited`, 5xx and transport errors to `ProviderUnavailable`, other 4xx to `ProviderError`; the key never appears in errors or `repr`.
- `providers.anthropic_generator`: `AnthropicGenerator` over the official SDK. `answer` forces the `answer_question` tool at temperature 0 and validates its input strictly; `rewrite_queries` asks the cheaper model for a JSON list and returns `[]` on any bad output. SDK retries are off (`max_retries=0`): `retry_transient` is the only retry layer.
- `factory.build_providers(settings)`: the fakes for `RAG_PROVIDER=fake`, the real adapters for `real` (clients are injectable for tests).

Defaults: `voyage-4` at 1024 dimensions, `rerank-2.5`, `claude-sonnet-5-5` for answers, `claude-haiku-5-5` for query rewriting. The Voyage names, limits and request fields were read on 2026-11-01 through a fetch tool that summarises pages, so **re-verify them on docs.voyageai.com before the first real call**; the Claude ids were checked against the installed SDK only. Changing the embedding model or dimension requires a re-index (new Qdrant collection): `RagSettings.index_name` is the collection name, and index entries are keyed by it, so either change makes every chunk pending for the new index. A model name that would make the index name longer than 128 characters is refused at startup.

## Settings

Environment variables only, validated when first read (the backend reads them at startup); an invalid value names the variable, never its value. The library's own settings use the `RAG_` prefix; the credentials and the Qdrant connection keep their usual, unprefixed names.

| Variable | Default | Meaning |
|---|---|---|
| `RAG_PROVIDER` | required | `fake` (deterministic fakes, no key: tests, CI, the dev stack) or `real` |
| `ANTHROPIC_API_KEY` | none | required and non-blank with `real` |
| `VOYAGE_API_KEY` | none | required and non-blank with `real` |
| `QDRANT_URL` | `http://127.0.0.1:6333` | Qdrant base URL |
| `QDRANT_API_KEY` | none | Qdrant API key (the Compose service requires one) |
| `RAG_EMBEDDING_MODEL`, `RAG_EMBEDDING_DIMENSION` | `voyage-4`, `1024` | together they name the index (`chunks_<model>_<dimension>`; with `RAG_PROVIDER=fake` the model part is `fake_<model>`, so fake vectors never share an index with the real model's) |
| `RAG_RERANK_MODEL` | `rerank-2.5` | Voyage reranker |
| `RAG_GENERATION_MODEL`, `RAG_REWRITE_MODEL` | `claude-sonnet-5-5`, `claude-haiku-5-5` | answer and query-rewriting models |
| `RAG_REWRITE_ENABLED`, `RAG_RERANK_ENABLED` | `true`, `true` | switch the optional stages off (for measurement) |
| `RAG_DENSE_CANDIDATES`, `RAG_SPARSE_CANDIDATES` | `50`, `50` | prefetch sizes of the hybrid search |
| `RAG_FUSED_CANDIDATES`, `RAG_FINAL_CANDIDATES` | `30`, `8` | kept after fusion, kept after reranking (the sources of the prompt) |
| `RAG_REFUSAL_THRESHOLD` | `0.2` | best rerank score under which the pipeline refuses without generating |
| `RAG_CHUNK_WORDS`, `RAG_CHUNK_OVERLAP_WORDS`, `RAG_MAX_CHUNKS_PER_DOCUMENT` | `400`, `60`, `500` | chunking |
| `RAG_EMBED_BATCH_SIZE` | `64` | texts per embedding request during indexing |
| `RAG_PROVIDER_TIMEOUT_SECONDS` | `20.0` | provider timeout; also the Qdrant client timeout, rounded up to whole seconds |
| `RAG_ASK_MAX_PER_HOUR` | `30` | questions per user and hour (`POST /api/v1/ask`) |
| `RAG_QUESTION_MAX_CHARS` | `2000` | declared but **not used**: the API enforces a fixed 2000 characters |

All numbers must be positive (and finite for floats).

## Test and check

From the repository root. Unit tests need no services:

```bash
uv run pytest ai/tests -m "not integration" --no-cov     # the 80% gate needs the full run
uv run pytest ai/tests                                    # needs DATABASE_URL (a PostgreSQL superuser), QDRANT_URL, QDRANT_API_KEY
uv run ruff check ai && uv run ruff format --check ai
uv run mypy --config-file ai/pyproject.toml ai/src ai/tests
```

Integration tests (`-m integration`) need `DATABASE_URL` (a PostgreSQL superuser, like `db/`: they create throwaway migrated databases) and `QDRANT_URL` / `QDRANT_API_KEY` (the Compose `qdrant` service; each test uses its own randomly named collection).

## Measure the quality

```bash
uv run pytest ai/tests/unit/test_measure.py --no-cov                  # scoring, report, question file
uv run python -X utf8 -m explorai_ai.measure --provider fake          # plumbing check: the numbers mean nothing
uv run python -X utf8 -m explorai_ai.measure --provider real --output docs/evaluation/rag-baseline-run.md
```

The real run needs the keys, `DATABASE_URL` and a populated public index, costs a few cents, and has **never been run**; the 14 annotated questions in `tests/fixtures/questions/questions.json` are **not validated by the maintainer** yet. Method and questions: [`docs/evaluation/rag-baseline.md`](../docs/evaluation/rag-baseline.md).

## Verified so far, and not

- Run: the unit suite (296 tests at the time of writing), ruff, strict mypy, the fake measurement.
- **Never run** (Docker was unresponsive during the whole sub-project): the integration tests of `ai/tests/integration` (store, index, retriever). CI is their first run; nothing merges before it is green.
- **Never called**: the real Voyage and Anthropic APIs. The adapters are tested with `httpx.MockTransport` and a stub client only.

## Known limits

- The quote returned is the model's text, matched after NFKC and case folding, not the source's exact span; the check proves the quote is in the chunk, not that it supports the claim; `[n]` markers in the answer are not rewritten when citations are dropped (ADR 0013).
- The quote floor counts whitespace-separated words, so a CJK quote without spaces never verifies. Short quotes ("42%") are refused.
- The sparse tokenizer drops every combining mark (over-folding Japanese dakuten, Thai and Indic signs) and makes a CJK run one token; folding depends on the Python `unicodedata` version; tokens are hashed to 32 bits (rare collisions); there is no BM25 length normalisation.
- The fakes tokenise ASCII only (they exist for tests, not for quality).
- Private chunk text and questions are sent to Voyage (all chunks at indexing; the question and up to 30 candidates per question) and Anthropic (the question and up to 8 chunks), whose data terms have not been reviewed (ADR 0014).
- A Qdrant 404 (missing alias or collection) reads as an empty corpus, so a wrong `QDRANT_URL` path looks like "nothing indexed" (logged at warning level). Old collections are not deleted after an alias switch.
- Database errors inside `ChunkStore.hydrate` escape as SQLAlchemy errors (the backend's handlers answer connection errors and pool timeouts with 503, like every other route). Rerank scores are not checked for finiteness in the retriever (the Voyage adapter already rejects non-finite ones).
- `measure.py`: the CLI sets `RAG_PROVIDER` in `os.environ` for the run; an empty expected quote would score as a hit; a Qdrant connection error at start-up gives a traceback instead of exit code 2.
