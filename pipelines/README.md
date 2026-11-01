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
- `flows/`: thin Prefect flows around `ingest.py`; `__main__.py` is the command line.
- `measure.py`: extraction quality on the annotated sample.

Replaying a batch changes nothing. A changed source file creates a new version of the same document. A new extractor version creates new versions from the stored bytes, with no network. Every batch also finishes what earlier runs left half-done: stored artifacts without a version for the current extractor are extracted at the end of the run (`recovered` in the run counters), so an item that dropped out of the latest listing is not lost. The title and metadata given by the source (arXiv API) win over the title found in the file.

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

A one-off run from the host, with PostgreSQL up and the variables exported:

```bash
set -a; . infra/.env; set +a
uv run python -m explorai_db.migrate
mkdir -p var/blobs
uv run python -m explorai_pipelines ingest-arxiv --query "cat:cs.CL" --max-results 5
uv run python -m explorai_pipelines ingest-web https://example.org/some-page
uv run python -m explorai_pipelines reextract --version pdf-2     # after bumping an extractor version
```

Inside the stack: `docker compose --env-file infra/.env -f infra/docker-compose.yml exec pipelines python -m explorai_pipelines ingest-arxiv --query cat:cs.CL --max-results 2`.

`ingest-web` allows only the hosts of the URLs you pass. To change what is ingested on schedule, edit the `NIGHTLY_*` constants in `__main__.py`.

## Test

```bash
uv run pytest pipelines/tests -m "not integration" --no-cov   # no services; includes a few seconds of PDF work
uv run pytest pipelines/tests                                  # needs DATABASE_URL (superuser), runs a temporary Prefect API
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
- Two runs at the same time are not coordinated (no lock): data stays consistent, but one run can report a collision as a failed item and the two in-memory duplicate indexes do not see each other.
