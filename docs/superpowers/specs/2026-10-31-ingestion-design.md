# Sub-project 1: Ingestion, design spec

Part of the master plan (`plan.md`). Date: 2026-10-31. Status: draft, awaiting review.

## 1. Goal

Build a reproducible, idempotent ingestion pipeline that turns heterogeneous sources (scientific papers from arXiv, web pages, an API) into clean, structured, versioned text stored in PostgreSQL, ready for the RAG sub-project.

The target corpus is **scientific articles**: arXiv PDFs and metadata through its official API, plus a few web pages (blogs, documentation). It is public, legal to ingest, and hard enough (columns, repeated headers, formulas) to make extraction quality worth measuring.

## 2. Scope

**In scope**
- Sources: arXiv (API for metadata, PDF download) and generic web pages.
- Text extraction from PDF (PyMuPDF) and HTML (trafilatura), split into sections.
- Cleaning, exact and near-duplicate detection.
- Storage: raw artifacts on a blob store, structured data in PostgreSQL.
- Orchestration and nightly scheduling with Prefect, retries, run reports.
- New workspace packages `db/` and `pipelines/`, a Prefect service in Docker Compose.

**Out of scope**
- Layout models for tables and formulas (Docling, Marker, GROBID), OCR for scanned PDFs: later improvement, measured in sub-project 11.
- Authentication, a `users` table, per-user filtering on read (sub-project 2).
- Chunking, embeddings, vector search (sub-project 3).
- Prompt-injection defence (sub-project 5); here only provenance is kept for audit.
- S3 or MinIO storage (sub-project 10); the blob store interface makes it a swap.
- Queue-driven workers (sub-project 11).

## 3. Decisions and rejected alternatives

| Topic | Decision | Alternatives not chosen |
|---|---|---|
| Pipeline shape | Layered: raw artifact, then extracted version, then sections. Raw is kept immutable. | Single pass storing text only: cannot re-extract without re-downloading (arXiv rate-limits), so results are not reproducible. Queue-driven workers: extra moving parts with no need yet. |
| Orchestration | Prefect (flows, tasks, retries, schedules, UI) | Airflow: standard but heavy (scheduler, webserver, metadata DB) for this need. APScheduler: no history or retry structure, would be reinvented. |
| PDF extraction | PyMuPDF, column handling, header and footer removal, section split | Plain text dump: columns mixed, poor RAG. Layout models: heavy (ML models, slow), deferred. |
| Ownership | Nullable `owner_id` on artifacts and documents: `NULL` is the shared public corpus | No owner: costly migration later and risk of a missing filter. Full users and auth now: overflows into sub-project 2. |
| Schema owner | Separate `db/` package (SQLAlchemy models, Alembic migrations) used by backend and pipelines | Schema inside `backend/`: couples the pipeline to the API. |
| Blob storage | `BlobStore` interface, `LocalBlobStore` on a Compose volume | Bytes in PostgreSQL: bloat. MinIO now: another service before it is needed. |

Each of these gets an ADR (Prefect, layered raw/normalised storage, `db/` package).

## 4. Repository layout added by this sub-project

```
explorai/
├── db/                          explorai-db: models, Alembic migrations, tests
│   └── src/explorai_db/
├── pipelines/                   explorai-pipelines
│   ├── src/explorai_pipelines/
│   │   ├── sources/             arxiv.py, web.py
│   │   ├── extract/             pdf.py, html.py
│   │   ├── clean.py, dedup.py, storage.py, repository.py
│   │   └── flows/               ingest_arxiv.py, ingest_web.py
│   └── tests/ (unit, integration, fixtures)
└── infra/docker-compose.yml     + prefect server, pipelines worker, blob volume
```

Both packages join the root `uv` workspace. `pipelines/` depends on `db/`, never on `backend/`. The backend adopts `db/` when it needs the schema (sub-project 2).

## 5. Data model (PostgreSQL)

| Table | Role | Key columns |
|---|---|---|
| `sources` | A declared source | `kind`, `name`, `config` |
| `raw_artifacts` | Downloaded bytes, immutable | `source_id`, `external_id`, `url`, `sha256`, `storage_key`, `fetched_at`, `owner_id` |
| `documents` | A logical document that can have several versions | `source_id`, `external_id`, `title`, `owner_id`, unique `(source_id, external_id, owner_id)` |
| `document_versions` | Text extracted from one artifact by one extractor | `document_id`, `artifact_id`, `extractor_version`, `text_hash`, `status`, `duplicate_of` |
| `sections` | Cleaned text split by section, input for chunking | `version_id`, `ordinal`, `heading`, `text` |
| `ingestion_runs` | One row per execution | `started_at`, `finished_at`, `status`, counters, errors |

`owner_id` has no foreign key until the `users` table exists (sub-project 2). `status` takes `ok`, `failed`, `unextractable`. Near-duplicates are marked through `duplicate_of`, never deleted.

## 6. Components

| Component | Role | Depends on |
|---|---|---|
| `sources/` | List and download. Return bytes and metadata, never write to the database. | `httpx` |
| `storage` | `BlobStore` interface: `put(bytes) -> key`, `get(key) -> bytes`. `LocalBlobStore` on a volume. | none |
| `extract/` | Bytes to structured sections. Pure functions, versioned by `extractor_version`. | PyMuPDF, trafilatura |
| `clean` | Unicode normalisation, repeated header and footer removal, hyphenated word joining. Pure. | none |
| `dedup` | Exact duplicates by hash, near-duplicates by MinHash with a documented threshold. Pure. | `datasketch` |
| `repository` | The only module that writes to the database (upserts, one transaction per document). | `db/` |
| `flows/` | Wire the steps into Prefect flows with retries and schedules. | the rest |

Extraction, cleaning and deduplication being pure functions makes them testable without network or database.

## 7. Data flow and idempotence

1. List the identifiers to ingest (arXiv query by category or search), honouring the rate limit.
2. For each identifier: download and compute the SHA-256. If this hash is already stored, stop (idempotence). Otherwise store the raw bytes and insert a `raw_artifacts` row.
3. Extract then clean, producing sections, and create a `document_version`.
4. Run deduplication and mark near-duplicates.
5. Record counters and errors in `ingestion_runs`.

Behaviour: replaying the same batch changes nothing; changed content at the source creates a new version of the same document; a new `extractor_version` creates new versions from stored artifacts without any network call. A nightly Prefect deployment runs the arXiv flow.

## 8. Error handling

- **Network:** 3 retries with exponential backoff, `Retry-After` honoured. After that the document is `failed` in `ingestion_runs` and the batch continues.
- **Unreadable or scanned PDF (no text):** status `unextractable`, never an exception that stops the flow. No OCR here.
- **Database write failure:** one transaction per document, so nothing partial. The stored raw artifact is kept and the step resumes on the next run.

## 9. Testing strategy

- **Unit:** extractors, cleaning and deduplication on versioned reference files: two-column PDF, repeated headers, hyphenated words, scanned PDF, corrupt file, HTML page.
- **Integration:** full pipeline against the real PostgreSQL of the Compose stack, with a simulated arXiv source (no network in CI).
- **Idempotence:** running the same batch twice leaves the database identical. This is the plan's success criterion, so it has a dedicated test.
- **Smoke:** `docker compose up`, a real batch of a few articles, rows checked in the database.
- Coverage gate stays at 80% for the new packages.

## 10. Threats and guardrails

| Threat | Guardrail |
|---|---|
| Malicious PDF (parser exploit) | Maximum size, extraction timeout, extraction in a separate process, non-root container. |
| Prompt injection hidden in a document | Text is stored as data, never executed. Real handling belongs to sub-project 5; here provenance (`source`, `sha256`) is kept for audit. |
| SSRF (pipeline follows a URL to the internal network) | Per-source domain allowlist, refusal of private addresses and `localhost`. |
| Abuse or blocking by arXiv | Documented rate limit, identifiable `User-Agent`, sequential downloads. |
| Cross-user leak | `owner_id` set at write time. Read-side filtering is tested in sub-project 2. |
| Secrets in pipeline config | Environment variables only, `.env.example` placeholders, `gitleaks` in hook and CI. |

## 11. Evaluation criteria (definition of done)

1. A heterogeneous batch (arXiv PDFs, web pages, API metadata) is ingested and a run report is produced.
2. Replaying the batch changes nothing (automated test).
3. A batch with pathological cases (scanned, corrupt, oversized) completes with the expected statuses.
4. A new `extractor_version` creates new versions from stored artifacts with no network access.
5. Extraction quality is measured on a hand-annotated sample of PDFs: the rate of pages without column mixing and the rate of headers and footers removed are reported in the documentation.
6. CI is green, and the documentation exists: READMEs for `pipelines/` and `db/`, the ADRs above, a concepts note with interview questions and answers.

## 12. Dependencies and next step

Depends on sub-project 0 (Compose, CI, conventions). After spec approval, write the implementation plan; no code or dependency is added before the plan is approved. Sub-project 2 (Core backend) follows.
