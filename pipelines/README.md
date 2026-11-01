# explorai-pipelines

The ingestion pipelines: they collect arXiv papers and web pages, extract and clean the text, mark duplicates and store everything in PostgreSQL. The design is in `docs/superpowers/specs/2026-10-31-ingestion-design.md`; the concepts are explained in `docs/concepts/01-ingestion.md`.

## How it works

```
source (list)  ->  safe fetch  ->  raw artifact  ->  extract  ->  clean  ->  dedup  ->  versions + sections
arXiv / web        allowlist,      blob store +     PDF/HTML     text        exact +     PostgreSQL
                   size cap        artifacts row                              near
```

- `sources/`: list items and never write to the database (`arxiv.py`, `web.py`). `ArxivSource` is the single gateway to arxiv.org, for the feed and for the PDFs: requests are spaced, carry the identifying `User-Agent`, and a `Retry-After` of 429/503 answers is waited out (capped at 120 s) before the task retries.
- `netsafe.py`: HTTPS-only, domain allowlist, private addresses refused, redirects re-validated, size cap enforced while streaming.
- Blob storage (`BlobStore`, `LocalBlobStore`, SHA-256 keys) lives in the shared `explorai_db.blobs` module.
- `extract/`: `pdf.py` (PyMuPDF, run in a separate process with a timeout) and `html.py` (trafilatura). Each is versioned (`pdf-1`, `html-1`).
- `clean.py`, `dedup.py`: pure text functions.
- `repository.py`: the only module that writes to the database, one transaction per call.
- `ingest.py`: one document's journey (`ingest_item`), batches (`run_batch`) and replays (`reextract`). No Prefect imports.
- `flows/`: thin Prefect flows around `ingest.py` and `indexing.py`; `__main__.py` is the command line.
- `indexing.py`: chunks, embeds and indexes the searchable versions into Qdrant, and prunes stale points (uses the `explorai-ai` library: chunking, sparse encoder, providers, `QdrantIndex`). No Prefect imports.
- `measure.py`: extraction quality on the annotated sample.

Uploaded private PDFs are processed here too: the `process-pending` deployment (every 30 seconds, `python -m explorai_pipelines process-pending` for one run) calls the same catch-up code (`reextract`) with `only_unversioned=True`: it reads only artifacts that have no version at all (`Repository.artifacts_without_any_version`), so a pass over a processed corpus reads no blob. Extractor version upgrades are left to the catch-up at the end of each nightly batch and to the manual `reextract` command, which read every artifact missing a version of a current extractor. Either way `reextract` takes the advisory lock `CATCH_UP_LOCK` (`pg_try_advisory_lock`, session level, non-blocking) so the nightly batch and `process-pending` never extract at the same time; a run that does not get the lock reports `skipped` and moves on. It then runs `collect_blobs`, which deletes blobs that no artifact references and that are older than one hour (`BLOB_GRACE_SECONDS`): the API never deletes blobs, and the grace period protects an upload whose rows are not committed yet. `serve` runs `nightly-arxiv` and `process-pending`, plus `index-pending` and `prune-index` when `RAG_PROVIDER` is set (see below). The concepts are in `docs/concepts/02-core-backend.md`.

Replaying a batch changes nothing. A changed source file creates a new version of the same document. A new extractor version creates new versions from the stored bytes, with no network. Every batch also finishes what earlier runs left half-done: stored artifacts without a version for the current extractor are extracted at the end of the run (`recovered` in the run counters), so an item that dropped out of the latest listing is not lost. The title and metadata given by the source (arXiv API) win over the title found in the file.

## Indexing for retrieval

The vector index is derived data: PostgreSQL decides what is searchable, Qdrant only holds vectors under the chunk ids, so the index can always be rebuilt. "Searchable" is one rule, `explorai_ai.store.is_eligible_version` (the latest `ok` version of a document, and only when it is canonical, `duplicate_of IS NULL`); every indexing query of `Repository` is built on it, and an integration test checks that the pipeline and the API's `ChunkStore` select exactly the same chunks.

- `index-pending` (every 30 seconds, advisory lock `INDEX_LOCK = 4242002`): cuts eligible versions that have no `chunked_at` yet into chunks (a version with zero chunks is still marked, so it is not chunked again), then embeds the chunks that have no `chunk_index_entries` row for the current index (`RagSettings.index_name`, the collection name of the configured model *and* dimension), in batches of `RAG_EMBED_BATCH_SIZE`, upserts them into that collection (dense vector from the provider, sparse vector computed locally, payload `owner_id` = the user id or `"public"`, `document_id`, `version_id`, no text) and records the entries after the upsert. A pass is idempotent: the point id is the chunk id and an entry is recorded once. A provider error stops the pass cleanly (`embedding_failed` = 1 in the counters, a warning with counts only): what was embedded is kept, the rest stays pending for the next pass. Only after a pass that leaves nothing pending does the alias `chunks_current` move to the current collection, so a model or dimension change fills a new collection in the background without touching what the API serves (the API searches the collection of its own configured model, not the alias); change the backend's model settings once the alias has moved. If the collection of the current index is missing while entries for it exist (Qdrant volume reset, collection deleted by hand), the pass first clears those entries (`reset_entries` in the counters) and rebuilds the collection from PostgreSQL, instead of recreating it empty under entries that claim it is full.
- `prune-index` (hourly, advisory lock `PRUNE_LOCK = 4242003`): deletes, in batches of 500, the points of the collection behind the alias whose chunk is no longer eligible (an older version, a duplicate, a deleted document). Point ids are listed before the eligible ids are read, so a point indexed during the prune is never deleted by it. Until then a stale point is harmless: the API re-reads every hit from PostgreSQL under Row-Level Security and the same eligibility rule, and drops it.

A run that does not get its lock does nothing and reports `skipped` = 1. Both commands need the RAG settings (`RAG_PROVIDER`, `QDRANT_URL`, `QDRANT_API_KEY` and the optional `RAG_*` values, see [`ai/README.md`](../ai/README.md#settings)); the Compose `pipelines` service passes them with `RAG_PROVIDER=fake` by default and waits for the `qdrant` health check. Without `RAG_PROVIDER`, `index-pending` and `prune-index` exit with a message naming the variable, and `serve` logs a warning and serves only the ingestion deployments. With `RAG_PROVIDER=real`, every chunk embedded here is sent to Voyage (ADR [0014](../docs/adr/0014-providers-behind-interfaces.md)).

The choices behind this design are in ADR [0011](../docs/adr/0011-qdrant-and-retrieval-isolation.md) and the concepts in [`docs/concepts/03-rag.md`](../docs/concepts/03-rag.md). The indexing integration tests (`tests/integration/test_indexing.py`, 14 tests) have **not been run yet** (Docker was unavailable); CI is their first run.

## Configuration

Environment variables only (see `.env.example`); the app fails at startup with a message naming the bad variable and never prints the database password.

| Variable | Meaning | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL DSN | required |
| `BLOB_DIR` | directory of the raw blob store | required |
| `CONTACT_EMAIL` | placed in the `User-Agent` sent to arXiv; use a real address | required |
| `MAX_DOWNLOAD_BYTES` | download size cap | 50000000 |
| `EXTRACT_TIMEOUT_SECONDS` | PDF extraction timeout | 60 |
| `ARXIV_MIN_INTERVAL_SECONDS` | minimum gap between arXiv API requests | 3.0 |

## Run

The nightly deployment runs in the Compose stack (`prefect` server on <http://127.0.0.1:4200>, `pipelines` service serving the schedule, `migrate` applying the migrations first):

```bash
cp .env.example infra/.env
docker compose --env-file infra/.env -f infra/docker-compose.yml up -d --build --wait
```

The `pipelines` container mounts the `blobs` volume that the backend also mounts. A one-off run from the host, with PostgreSQL up and the variables exported:

```bash
set -a; . infra/.env; set +a
uv run python -m explorai_db.migrate
mkdir -p var/blobs
uv run python -m explorai_pipelines ingest-arxiv --query "cat:cs.CL" --max-results 5
uv run python -m explorai_pipelines ingest-web https://example.org/some-page
uv run python -m explorai_pipelines reextract --version pdf-2     # after bumping an extractor version
uv run python -m explorai_pipelines process-pending               # one pass over uploads, then blob cleanup
uv run python -m explorai_pipelines index-pending                 # one indexing pass (needs RAG_PROVIDER and Qdrant)
uv run python -m explorai_pipelines prune-index                   # delete stale points from the live collection
```

Inside the stack: `docker compose --env-file infra/.env -f infra/docker-compose.yml exec pipelines python -m explorai_pipelines ingest-arxiv --query cat:cs.CL --max-results 2`.

`ingest-web` allows only the hosts of the URLs you pass. To change what is ingested on schedule, edit the `NIGHTLY_*` constants in `__main__.py`.

## Test

```bash
uv run pytest pipelines/tests -m "not integration" --no-cov   # no services; includes a few seconds of PDF work
uv run pytest pipelines/tests                                  # needs DATABASE_URL (superuser) and QDRANT_URL, runs a temporary Prefect API
uv run ruff check pipelines && uv run ruff format --check pipelines
uv run mypy --config-file pipelines/pyproject.toml pipelines/src pipelines/tests
```

No test touches the network. PDF fixtures are generated in code (`explorai_pipelines.testing`); the annotated sample in `tests/fixtures/annotated/` is real CC-BY arXiv papers.

## Measure the extraction

```bash
uv run python -X utf8 -m explorai_pipelines.measure
```

Results and the failure analysis are in `docs/evaluation/extraction-baseline.md`.

## Known limits

- Scanned PDFs are stored as `unextractable`; there is no OCR.
- Tables and formulas come out as plain text lines.
- The download check resolves a name and the HTTP client resolves it again, so a DNS-rebinding attacker is not fully excluded.
- The near-duplicate scan is linear in the number of stored signatures.
- The `failed` version status exists in the schema but is not written: an unexpected error, a timeout or a killed extraction worker stores no version, so the next run retries it. Only a content verdict (`unextractable`) is stored.
- Duplicates are searched within one owner only: a private document is never the canonical version of a public one.
- The catch-up extraction is serialised by an advisory lock, but two ingestion batches (`ingest-arxiv`, `ingest-web`) at the same time are not coordinated: data stays consistent, but one run can report a collision as a failed item and the two in-memory duplicate indexes do not see each other.
- An upload that keeps failing for a transient reason is retried every 30 seconds; there is no retry cap yet.
- Indexing: a permanent provider error (a bad key) looks like an outage: the pass stops, counts `embedding_failed` and the flow stays green. A Qdrant error raises out of the pass. One version that fails deterministically during chunking blocks the indexing of all others. A full re-embed after a model change runs under one lock with no per-pass cap. A document deleted during a pass can make recording its entries fail on a foreign key violation. Eligible ids and point ids are loaded in memory by `prune-index`. Old collections are never deleted after an alias switch. Invalid RAG settings also stop the ingestion commands, which share `build_runtime`.
