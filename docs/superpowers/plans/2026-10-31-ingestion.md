# Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible, idempotent ingestion pipeline that turns arXiv papers and web pages into clean, versioned, sectioned text in PostgreSQL, orchestrated and scheduled by Prefect.

**Architecture:** Two new `uv` workspace packages. `db/` (`explorai_db`) owns the SQLAlchemy models and Alembic migrations. `pipelines/` (`explorai_pipelines`) holds pure steps (safe fetch, extract, clean, dedup), a `Repository` that is the only writer, an `ingest` module that assembles one document's journey (raw artifact, then version, then sections), and thin Prefect flows. Raw bytes are immutable and content-addressed on a `BlobStore`; extraction can be replayed from stored bytes with no network.

**Tech Stack:** Python 3.12, SQLAlchemy 2 (sync) with psycopg 3, Alembic, Prefect 3, httpx, PyMuPDF, trafilatura, datasketch, pytest, ruff, mypy strict; Docker Compose, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-31-ingestion-design.md` (read it first; also `plan.md` for tooling rationale and `docs/superpowers/plans/2026-10-31-foundations.md` for the conventions this plan continues).

## Global Constraints

- Everything written in the repo is in English. The user is learning: each task has a **Concept** line, and Task 11 turns them into `docs/concepts/01-ingestion.md` with interview questions and answers.
- GitFlow. Work happens on `feature/ingestion` (already cut from `develop`, spec already committed). Conventional Commits, each ending with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Never push, open a PR or change GitHub settings without the user's explicit approval.
- `pipelines/` depends on `db/`, never on `backend/`. `ai/` does not exist yet and is not created.
- Same quality bar as the backend: `mypy` strict, `ruff` with the backend rule set, `pytest` with an 80% coverage gate per package. Libraries without type stubs (pymupdf, trafilatura, datasketch, prefect internals) get a `[[tool.mypy.overrides]]` with `ignore_missing_imports`, nothing broader.
- Configuration only from environment variables, validated at startup (pydantic-settings, `hide_input_in_errors=True`, same pattern as `backend/src/explorai_backend/config.py`). Hosts use `127.0.0.1`, never `localhost`.
- Statuses of a `document_version`: exactly `ok`, `failed`, `unextractable`. Item outcomes: exactly `unchanged`, `ingested`, `new_version`, `unextractable`, `failed`.
- `owner_id` is a nullable UUID column with no foreign key. `NULL` means the shared public corpus. Uniqueness that involves `owner_id` must use PostgreSQL `NULLS NOT DISTINCT` (PostgreSQL 16 supports it).
- Safety limits: download cap `MAX_DOWNLOAD_BYTES` (default 50_000_000), extraction timeout `EXTRACT_TIMEOUT_SECONDS` (default 60), arXiv minimum interval between requests `ARXIV_MIN_INTERVAL_SECONDS` (default 3.0), `CONTACT_EMAIL` required (used in the `User-Agent`).
- No network access in any automated test. Fixtures are generated in code or committed as small files.
- Containers non-root, ports bound to `127.0.0.1`, images pinned by exact tag, no real secrets in the repo.
- Plan-level choices (not in the spec): sync SQLAlchemy (no event-loop issues on Windows, simpler for batch work); a `minhash_signature` column on `document_versions` (near-duplicate index is rebuilt from it); Prefect server uses its default SQLite on a Compose volume (development only) and the flow is served by `flow.serve(cron=...)` in a `pipelines` container, no work pool; a one-shot `migrate` Compose service runs Alembic before the pipelines start.

## Review Focus

1. A PDF with no extractable text (scanned) or corrupt bytes: status `unextractable` or `failed`, the batch keeps going. (Tasks 5, 7)
2. A hostile download: the size cap is enforced while streaming, so an oversized body is never fully read. (Task 3)
3. A redirect to a private or loopback address, or to a domain outside the allowlist, is refused on every hop. (Task 3)
4. `owner_id = NULL` rows must still be unique: inserting the same artifact or document twice for the public corpus must violate the constraint. (Task 2)
5. A failure halfway through one document leaves no partial rows, and the next run completes it. (Task 7)

---

### Task 1: Package scaffolding and tooling

**Files:**
- Modify: `pyproject.toml` (workspace members `["backend", "db", "pipelines"]`)
- Create: `db/pyproject.toml`, `db/README.md`, `db/src/explorai_db/__init__.py`, `pipelines/pyproject.toml`, `pipelines/README.md`, `pipelines/src/explorai_pipelines/__init__.py`, `db/tests/conftest.py`, `pipelines/tests/conftest.py`
- Modify: `.pre-commit-config.yaml` and `.github/dependabot.yml` only if they hardcode `backend` paths

**Interfaces:**
- Produces: two importable packages `explorai_db` and `explorai_pipelines` in the workspace; `explorai-pipelines` depends on `explorai-db` through `[tool.uv.sources] explorai-db = { workspace = true }`.

**Concept:** uv workspace with several members and one lockfile; why a shared `db/` package (spec section 3).

- [ ] **Step 1:** Copy the tooling sections of `backend/pyproject.toml` (ruff, mypy, pytest with `integration` marker and `--cov=<package> --cov-fail-under=80`, hatchling src layout) into both new `pyproject.toml` files, with `known-first-party` and `--cov` set to the new package name. `db` dependencies: `sqlalchemy`, `alembic`, `psycopg[binary]`. `pipelines` dependencies: `explorai-db`, `pydantic-settings`, `prefect`, `httpx`, `pymupdf`, `trafilatura`, `datasketch`; dev group like the backend's. Add the mypy overrides named in Global Constraints.
- [ ] **Step 2:** Both `tests/conftest.py` files: no event-loop policy needed (sync code); keep a docstring only.
- [ ] **Step 3:** Run `uv sync` then `uv run ruff check db pipelines` and `uv run mypy db/src pipelines/src`. Expected: all clean on the empty packages.
- [ ] **Step 4:** Add one test per package that imports it (`import explorai_db`, `import explorai_pipelines`) and run `uv run pytest db/tests pipelines/tests --no-cov`. Expected: pass. The 80% coverage gate stays configured but is only meaningful once Tasks 2 and 3 add code, so do not run it with coverage in this task.
- [ ] **Step 5: Commit:** `build: add db and pipelines workspace packages`.

---

### Task 2: Database schema and migrations

**Files:**
- Create: `db/src/explorai_db/models.py`, `db/src/explorai_db/session.py`, `db/alembic.ini`, `db/migrations/env.py`, `db/migrations/versions/0001_initial.py`
- Test: `db/tests/unit/test_session.py`, `db/tests/integration/test_schema.py`

**Interfaces:**
- Produces: `Base`, `Source`, `RawArtifact`, `Document`, `DocumentVersion`, `Section`, `IngestionRun` in `explorai_db.models` with the columns of spec section 5 plus `DocumentVersion.minhash_signature: bytes | None`; `make_engine(database_url: str) -> Engine` (rewrites `postgresql://` to `postgresql+psycopg://`); `make_session_factory(engine: Engine) -> sessionmaker[Session]`.
- Unique constraints, all `postgresql_nulls_not_distinct=True`: `documents (source_id, external_id, owner_id)`, `raw_artifacts (source_id, external_id, owner_id, sha256)`. Also unique `document_versions (artifact_id, extractor_version)`. A check constraint limits `document_versions.status` to `ok|failed|unextractable`. `duplicate_of` is a nullable self-reference to `document_versions.id`. Primary keys are UUIDs generated in Python.

**Concept:** schema as code, migrations, why `NULL` is a trap in unique constraints.

- [ ] **Step 1: Write failing tests.** Unit: `make_engine("postgresql://u:p@h/db")` yields a URL with driver `psycopg`; an already-qualified URL is left alone. Integration (marker `integration`, env `DATABASE_URL`): `test_upgrade_head_creates_all_tables`, `test_models_match_migrations` (Alembic `compare_metadata` against the migrated database reports no diff), `test_public_corpus_artifact_is_unique` (insert the same `(source, external_id, NULL owner, sha256)` twice, expect `IntegrityError`), `test_public_corpus_document_is_unique`, `test_invalid_status_rejected`.
- [ ] **Step 2:** Run the new tests, expect failure (modules missing).
- [ ] **Step 3:** Implement the models with SQLAlchemy 2 typed declarative style and `session.py`. Write `env.py` so the URL comes from `DATABASE_URL` (via `make_engine`), never from `alembic.ini`. Generate `0001_initial.py` with `alembic revision --autogenerate`, then read it and fix anything autogenerate missed (the NULLS NOT DISTINCT flags, the check constraint).
- [ ] **Step 4:** Start the stack (`docker compose --env-file infra/.env -f infra/docker-compose.yml up -d --wait postgres`), export the env, run `uv run alembic -c db/alembic.ini upgrade head` then `uv run pytest db/tests`. Expected: pass; also run `downgrade base` once to prove the downgrade works.
- [ ] **Step 5: Commit:** `feat(db): add ingestion schema and initial migration`.

---

### Task 3: Blob store and safe HTTP fetch

**Files:**
- Create: `pipelines/src/explorai_pipelines/config.py`, `storage.py`, `netsafe.py`
- Test: `pipelines/tests/unit/test_config.py`, `test_storage.py`, `test_netsafe.py`

**Interfaces:**
- Produces: `PipelineSettings` (fields `database_url: PostgresDsn`, `blob_dir: Path`, `contact_email: str`, `max_download_bytes: int`, `extract_timeout_seconds: float`, `arxiv_min_interval_seconds: float`, all validated, `from_env()`, cached `get_settings()`); `BlobStore` (Protocol: `put(data: bytes) -> str`, `get(key: str) -> bytes`); `LocalBlobStore(root: Path)` where the key is the SHA-256 hex of the bytes (content-addressed, so `put` is idempotent) and any key that is not 64 lowercase hex characters raises `ValueError`; `UrlPolicy(allowed_domains: frozenset[str])`; `UnsafeUrlError`, `DownloadTooLargeError`; `FetchResult(content: bytes, content_type: str | None, final_url: str)`; `fetch_bytes(client: httpx.Client, url: str, policy: UrlPolicy, max_bytes: int) -> FetchResult`.

**Concept:** content-addressed storage; SSRF and why redirects must be re-checked; streaming with a size cap.

- [ ] **Step 1: Write failing tests.**
  - config: missing `CONTACT_EMAIL` names the variable in the error and the DSN password never appears; non-positive limits rejected.
  - storage: `test_put_get_roundtrip`, `test_put_is_idempotent` (same bytes, same key, one file), `test_traversal_key_rejected` (`"../x"`, short keys, uppercase).
  - netsafe, using `httpx.MockTransport` and a stub resolver injected as a parameter of `fetch_bytes`-level helper `resolve: Callable[[str], list[str]]` (default real DNS): `test_domain_outside_allowlist_refused`, `test_non_https_scheme_refused` (allow `http` only when the policy says so; default https only), `test_private_ip_refused` (`10.x`, `192.168.x`, `127.0.0.1`, `169.254.x`, `::1`), `test_redirect_to_private_address_refused`, `test_redirect_to_other_domain_refused`, `test_redirect_loop_stops` (cap 5 hops), `test_oversized_body_aborted_while_streaming` (the iterator is not consumed past the cap; assert bytes read ≤ cap + one chunk), `test_declared_content_length_too_large_refused_before_reading`.
- [ ] **Step 2:** Run, expect failures.
- [ ] **Step 3:** Implement. `fetch_bytes` follows redirects manually (client with `follow_redirects=False`), re-validating scheme, domain allowlist (exact or subdomain match) and resolved IPs on every hop, and streams the body in chunks counting bytes.
- [ ] **Step 4:** `uv run pytest pipelines/tests -m "not integration"`, `ruff`, `mypy`. Expected: clean.
- [ ] **Step 5: Commit:** `feat(pipelines): add blob store, settings and SSRF-safe fetch`.

---

### Task 4: Cleaning and deduplication

**Files:**
- Create: `pipelines/src/explorai_pipelines/clean.py`, `dedup.py`
- Test: `pipelines/tests/unit/test_clean.py`, `test_dedup.py`

**Interfaces:**
- Produces: `normalize(text: str) -> str` (Unicode NFKC, control characters removed, runs of whitespace collapsed per line, blank-line runs collapsed to one); `join_hyphenated(text: str) -> str` (`"infor-\nmation"` becomes `"information"`, but a real hyphen at a line end followed by a capital or a digit is kept); `remove_repeated_lines(pages: list[str], min_page_ratio: float = 0.5) -> list[str]` (a first or last line, ignoring digits, that repeats on at least `min_page_ratio` of the pages is removed from those pages; needs at least 3 pages); `text_hash(text: str) -> str` (SHA-256 hex of normalized text); `minhash_signature(text: str, num_perm: int = 128) -> bytes` (word 5-shingles); `NearDuplicateIndex(threshold: float = 0.9, num_perm: int = 128)` with `add(key: str, signature: bytes) -> None` and `find(signature: bytes) -> str | None`.

**Concept:** normalization, MinHash and Jaccard similarity, why near-duplicates are marked and never deleted.

- [ ] **Step 1: Write failing tests** with literal strings: hyphenation join and the kept-hyphen exception; header `"Journal of X 12"` / `"Journal of X 13"` repeated on 4 of 5 pages is removed while a unique first line is kept; fewer than 3 pages leaves input untouched; `text_hash` is equal for texts differing only in whitespace and Unicode form; `test_near_duplicate_found` (a 200-word text vs the same text with 3 words changed, threshold 0.9, is found); `test_unrelated_text_not_found`; `test_signature_is_deterministic`.
- [ ] **Step 2:** Run, expect failures.
- [ ] **Step 3:** Implement with `unicodedata` and `datasketch.MinHash`/`MinHashLSH`; `find` returns the key of the first match above the threshold.
- [ ] **Step 4:** Run tests, ruff, mypy. Expected: clean.
- [ ] **Step 5: Commit:** `feat(pipelines): add text cleaning and near-duplicate detection`.

---

### Task 5: PDF and HTML extraction

**Files:**
- Create: `pipelines/src/explorai_pipelines/extract/__init__.py`, `models.py`, `pdf.py`, `html.py`
- Test: `pipelines/tests/unit/test_extract_pdf.py`, `test_extract_html.py`, `pipelines/tests/pdf_factory.py` (builds fixture PDFs in code with PyMuPDF)

**Interfaces:**
- Produces: `ExtractedSection(heading: str | None, text: str)`, `ExtractedDocument(title: str | None, sections: list[ExtractedSection])` (frozen dataclasses); `Unextractable(Exception)`; constants `PDF_EXTRACTOR_VERSION = "pdf-1"`, `HTML_EXTRACTOR_VERSION = "html-1"`; `extract_pdf(data: bytes) -> ExtractedDocument`; `extract_pdf_isolated(data: bytes, timeout_seconds: float) -> ExtractedDocument` (runs `extract_pdf` in a separate spawned process, kills it on timeout and raises `Unextractable`); `extract_html(data: bytes, url: str | None = None) -> ExtractedDocument`.
- `extract_pdf` reads each page's text blocks in reading order (left column before right when blocks form two columns), cleans pages with `remove_repeated_lines`, `join_hyphenated` and `normalize` from Task 4, then splits sections on lines that look like numbered or all-caps headings (`1 Introduction`, `Abstract`, `References`); text before the first heading goes in a section with `heading=None`. Raises `Unextractable` when the cleaned text has fewer than 50 characters or the bytes are not a valid PDF.

**Concept:** why PDF text is hard (a page is drawing instructions), reading order, process isolation as a guardrail.

- [ ] **Step 1: Write failing tests.** `pdf_factory` helpers create: a one-column PDF with headings, a two-column PDF where the left column contains "ALPHA first sentence" and the right "OMEGA last sentence", a 5-page PDF with a repeated running header and page numbers, a PDF whose page is an image only (no text layer), and truncated random bytes. Tests: `test_two_columns_read_left_then_right` (ALPHA before OMEGA in the output), `test_running_header_removed`, `test_headings_split_into_sections`, `test_scanned_pdf_unextractable`, `test_corrupt_bytes_unextractable`, `test_isolated_timeout_kills_worker` (monkeypatch the worker target to sleep; expect `Unextractable` within the timeout plus one second and no leftover process), `test_html_main_content_without_navigation` (page with nav, footer, article), `test_html_without_content_unextractable`.
- [ ] **Step 2:** Run, expect failures.
- [ ] **Step 3:** Implement. For the isolated worker use `multiprocessing.get_context("spawn")` (required on Windows) and return the result through a `Queue`; the worker function must be importable at module level.
- [ ] **Step 4:** Run the extraction tests, ruff, mypy. Expected: clean.
- [ ] **Step 5: Commit:** `feat(pipelines): add PDF and HTML extractors with isolation and timeout`.

---

### Task 6: Sources (arXiv and web)

**Files:**
- Create: `pipelines/src/explorai_pipelines/sources/__init__.py`, `base.py`, `arxiv.py`, `web.py`
- Test: `pipelines/tests/unit/test_arxiv_source.py`, `test_web_source.py`, `pipelines/tests/fixtures/arxiv_feed.xml`, `arxiv_empty.xml`, `arxiv_malformed.xml`

**Interfaces:**
- Produces: `SourceItem(external_id: str, url: str, title: str | None, metadata: dict[str, str])` (frozen); `ArxivSource(client: httpx.Client, settings: PipelineSettings, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic)` with `list_items(query: str, max_results: int) -> list[SourceItem]`; `WebSource(urls: Sequence[str])` with `list_items() -> list[SourceItem]` (external id is the URL with fragment dropped); `ARXIV_POLICY = UrlPolicy(frozenset({"arxiv.org", "export.arxiv.org"}))`; `source_kind` strings `"arxiv"` and `"web"`.
- `ArxivSource` calls `https://export.arxiv.org/api/query` with `search_query`, `max_results`, `sortBy=submittedDate`; the `User-Agent` is `explorai-ingestion (mailto:<contact_email>)`; `external_id` is the arXiv id without version suffix (`2401.01234`); `url` is the entry's PDF link; metadata keeps `published`, `authors`, `summary`. It never makes two requests closer than `arxiv_min_interval_seconds` (the injected clock and sleep make this testable).

**Concept:** polite crawling, rate limits, Atom feeds.

- [ ] **Step 1: Write failing tests** with `httpx.MockTransport` and the fixture feeds: `test_parses_entries` (ids, pdf urls, titles, version suffix stripped), `test_user_agent_contains_contact`, `test_requests_are_spaced` (two calls, fake clock, assert recorded sleep ≥ interval minus elapsed), `test_empty_feed_returns_empty_list`, `test_malformed_feed_raises_source_error` (a `SourceError` defined in `base.py`), `test_http_error_raises_source_error`, `test_web_source_drops_fragment_and_dedups_urls`.
- [ ] **Step 2:** Run, expect failures. **Step 3:** Implement with `xml.etree.ElementTree` on a feed that was size-checked first (parse errors become `SourceError`; do not use a parser that resolves external entities). **Step 4:** Run tests, ruff, mypy.
- [ ] **Step 5: Commit:** `feat(pipelines): add arXiv and web sources`.

---

### Task 7: Repository and ingestion pipeline

**Files:**
- Create: `pipelines/src/explorai_pipelines/repository.py`, `ingest.py`
- Test: `pipelines/tests/unit/test_ingest_unit.py`, `pipelines/tests/integration/test_ingest.py`

**Interfaces:**
- Consumes: Task 2 models and session helpers, Task 3 `BlobStore`/`FetchResult`/`fetch_bytes`, Task 4 hashes and `NearDuplicateIndex`, Task 5 extractors and `Unextractable`, Task 6 `SourceItem`.
- Produces: `Outcome` (str enum: `unchanged|ingested|new_version|unextractable|failed`); `Repository(session_factory: sessionmaker[Session])` with `get_or_create_source(kind: str, name: str, config: dict[str, str]) -> UUID`, `start_run(source_id: UUID) -> UUID`, `finish_run(run_id: UUID, counters: dict[str, int], errors: list[str]) -> None`, `find_artifact(source_id, external_id, owner_id, sha256) -> UUID | None`, `store_artifact(...) -> UUID`, `save_version(artifact_id, extracted: ExtractedDocument | None, extractor_version, status, text_hash, signature, duplicate_of) -> UUID` (one transaction: version plus all sections, or nothing), `load_signatures() -> list[tuple[str, bytes]]`, `artifacts_without_version(extractor_version: str) -> list[ArtifactRef]`; `ingest_item(item, fetched: FetchResult, *, repo, store, source_id, owner_id=None, settings) -> Outcome`; `reextract(*, repo, store, extractor_version: str, settings) -> dict[str, int]` (counters by outcome); `run_batch(items_with_fetch, ...) -> RunReport`.
- `ingest_item` order: compute SHA-256; if `find_artifact` hits, return `unchanged` without touching anything; else `store.put`, `store_artifact`, choose the extractor from content type (`application/pdf` or `.pdf` url means PDF, otherwise HTML), run it (PDF through `extract_pdf_isolated`), clean, hash, signature, query the near-duplicate index (exact hash match is also a duplicate), `save_version`. A new artifact for an existing `(source, external_id, owner)` document yields `new_version`; otherwise `ingested`. Exceptions of one item are caught, recorded, outcome `failed`, and never abort `run_batch`.

**Concept:** idempotency keys, transactions per unit of work, immutable raw layer, replaying from raw.

- [ ] **Step 1: Write failing integration tests** (real Postgres, temporary `LocalBlobStore`, in-memory fake fetched results built with the Task 5 `pdf_factory`):
  - `test_ingest_creates_document_version_and_sections`
  - `test_replaying_same_batch_changes_nothing` (snapshot row counts and ids of every table except `ingestion_runs`, run twice, equal; second run reports all `unchanged`). **This is the plan's success criterion.**
  - `test_changed_content_creates_new_version_of_same_document` (same external id, different bytes: one `documents` row, two `raw_artifacts`, outcome `new_version`)
  - `test_reextract_with_new_extractor_version_needs_no_network` (the fetch function is replaced by one that raises; `reextract` with `"pdf-2"` creates new versions from stored bytes and leaves the old ones)
  - `test_scanned_pdf_marked_unextractable_and_batch_continues` (3 items, the middle one scanned: counters show 2 ok, 1 unextractable, run finishes)
  - `test_failure_leaves_no_partial_rows` (make `save_version` fail after sections are staged, for instance by passing a section with a value violating a constraint; assert zero `sections` and zero `document_versions` rows for that item, then a retry succeeds)
  - `test_exact_and_near_duplicates_are_marked_not_deleted` (`duplicate_of` set, both versions present)
  - `test_owner_id_is_stored` (private item gets its owner, public stays NULL, same bytes for two owners produce two artifacts)
- [ ] **Step 2:** Run, expect failures.
- [ ] **Step 3:** Implement. Keep `ingest.py` free of Prefect imports. `run_batch` writes `ingestion_runs` through `start_run`/`finish_run` in a `try/finally` so an aborted run still ends with status `failed`.
- [ ] **Step 4:** Run the whole `pipelines` suite with Postgres up; ruff; mypy. Expected: pass, coverage ≥ 80%.
- [ ] **Step 5: Commit:** `feat(pipelines): add repository and idempotent ingestion pipeline`.

---

### Task 8: Prefect flows and scheduling

**Files:**
- Create: `pipelines/src/explorai_pipelines/flows/__init__.py`, `ingest_arxiv.py`, `ingest_web.py`, `reextract.py`, `pipelines/src/explorai_pipelines/__main__.py`
- Test: `pipelines/tests/integration/test_flows.py`

**Interfaces:**
- Produces: `ingest_arxiv_flow(query: str, max_results: int = 20) -> RunReport`, `ingest_web_flow(urls: list[str]) -> RunReport`, `reextract_flow(extractor_version: str) -> dict[str, int]`, each decorated with `@flow` and tasks with `@task(retries=3, retry_delay_seconds=[2, 10, 30])` around network calls only (fetch, source listing), never around database writes. `__main__` offers `python -m explorai_pipelines ingest-arxiv --query ... --max-results N`, `ingest-web URL...`, `reextract --version V`, and `serve` which calls `ingest_arxiv_flow.serve(name="nightly-arxiv", cron="0 2 * * *", parameters={"query": "cat:cs.CL", "max_results": 20})`.
- `RunReport` is a frozen dataclass: `run_id: UUID`, `status: str`, `counters: dict[str, int]`, `errors: list[str]`.

**Concept:** flow vs task, retries and idempotence (why retrying is safe here), scheduling.

- [ ] **Step 1: Write failing tests** using `prefect.testing.utilities.prefect_test_harness()` and a `MockTransport`-backed client injected through a module-level factory that the test replaces: `test_arxiv_flow_ingests_and_reports`, `test_flow_retries_transient_network_error` (first call 503, second 200; the item ends `ingested`), `test_failed_item_does_not_fail_the_flow` (flow state completed, report counts one failed, `ingestion_runs.status` is `completed_with_errors`), `test_reextract_flow_reports_counters`.
- [ ] **Step 2:** Run, expect failures. **Step 3:** Implement; flows only wire the Task 3 to 7 functions, no business logic. **Step 4:** Run tests, ruff, mypy.
- [ ] **Step 5: Commit:** `feat(pipelines): add Prefect flows, CLI and nightly schedule`.

---

### Task 9: Compose, Docker image, smoke test and CI

**Files:**
- Create: `pipelines/Dockerfile` (multi-stage like `backend/Dockerfile`: uv builder, python:3.12.16-slim runtime, non-root uid 10001), `db/Dockerfile` or a stage in the pipelines image that runs `alembic` (one image is enough; choose one and say which in the commit)
- Modify: `infra/docker-compose.yml`, `infra/smoke.sh`, `.env.example`, `.dockerignore`, `.github/workflows/ci.yml`, `.pre-commit-config.yaml` (only if paths are hardcoded), `.github/dependabot.yml`

**Interfaces:**
- Compose adds: `migrate` (one-shot, `alembic -c db/alembic.ini upgrade head`, `depends_on` postgres healthy, `restart: "no"`), `prefect` (pinned `prefecthq/prefect` exact tag, `prefect server start --host 0.0.0.0`, SQLite on a named volume, port `127.0.0.1:4200`, healthcheck on `/api/health`), `pipelines` (runs `python -m explorai_pipelines serve`, `depends_on` migrate `service_completed_successfully` and prefect healthy, `PREFECT_API_URL=http://prefect:4200/api`, blob volume mounted at `/data/blobs`, healthcheck that the process is alive).
- `.env.example` adds `BLOB_DIR`, `CONTACT_EMAIL`, `MAX_DOWNLOAD_BYTES`, `EXTRACT_TIMEOUT_SECONDS`, `ARXIV_MIN_INTERVAL_SECONDS` with placeholders; `CONTACT_EMAIL` uses `dev@example.invalid`.
- CI: the `backend` job also runs `ruff check`, `ruff format --check`, `mypy` for `db` and `pipelines`, and `uv run pytest db/tests pipelines/tests` (each package keeps its own coverage gate through its own `pyproject.toml`); add `CONTACT_EMAIL` and `BLOB_DIR` env to that job. Keep the five job names. Actions stay SHA-pinned.
- `smoke.sh`: after the existing checks, assert `migrate` exited 0, the `prefect` health endpoint answers, the `pipelines` service is healthy, and the `sources` table exists (`psql -c "select 1 from sources limit 1"` through the postgres container). A real network batch of two arXiv papers runs only when `SMOKE_NETWORK=1` is set; CI leaves it unset so the job stays deterministic.

**Concept:** one-shot migration containers, ordering with `service_completed_successfully`, why CI must not depend on a third-party API.

- [ ] **Step 1:** Extend `smoke.sh` first (it is the test): run it and watch it fail on the missing services. **Step 2:** Add the Dockerfile, Compose services and env changes. **Step 3:** `bash infra/smoke.sh` until it passes, then once with `SMOKE_NETWORK=1` and show the two ingested rows. **Step 4:** Update CI and run the same commands locally (`uv run ruff check backend db pipelines`, mypy, both pytest suites, pre-commit on all files).
- [ ] **Step 5: Commit:** `feat(infra): add prefect, migration and pipelines services; extend CI`.

---

### Task 10: Extraction quality measurement

**Files:**
- Create: `pipelines/scripts/measure_extraction.py`, `pipelines/tests/fixtures/annotated/` (3 to 5 small CC-BY arXiv PDFs plus one `<name>.json` annotation each), `pipelines/tests/unit/test_measure_extraction.py`, `docs/evaluation/extraction-baseline.md`

**Interfaces:**
- Annotation file: `{"order_pairs": [["sentence that comes first", "sentence that comes later"], ...], "repeated_lines": ["running header text", ...]}`.
- Script: `uv run python pipelines/scripts/measure_extraction.py` prints and writes, per file and overall, `column_order_rate` (fraction of `order_pairs` where both strings are found and the first precedes the second in the extracted text) and `header_removal_rate` (fraction of `repeated_lines` absent from the extracted text), plus the `extractor_version`.

**Concept:** measuring before improving; a baseline makes sub-project 11 comparable.

- [ ] **Step 1:** Write a failing unit test for the scoring functions on tiny synthetic input (`score_order`, `score_headers` returning floats; a missing sentence counts as a failed pair).
- [ ] **Step 2:** Implement the script on top of `extract_pdf`. **Step 3:** Choose the sample: papers released under a CC-BY licence (verify the licence on each arXiv abstract page), pick at least one two-column paper with running headers; write the annotations by reading the PDFs. These annotations are the author's ground truth, so **flag them to the user for review** in the task report; do not claim a quality number without that review.
- [ ] **Step 4:** Run the script, record the numbers, date and `extractor_version` in `docs/evaluation/extraction-baseline.md` with how to reproduce, and note any failure modes seen (formulas, tables) as input for sub-project 11.
- [ ] **Step 5: Commit:** `feat(pipelines): measure PDF extraction quality on an annotated sample`.

---

### Task 11: Documentation

**Files:**
- Create: `docs/adr/0006-prefect-for-orchestration.md`, `0007-layered-raw-and-normalised-storage.md`, `0008-shared-db-package.md`, `docs/concepts/01-ingestion.md`
- Modify: `docs/adr/README.md`, `README.md`, `CONTRIBUTING.md` (only if commands changed), `CLAUDE.md` (Current state, Commands, layout), `plan.md` (sub-project 1 marked implemented, next step), `db/README.md`, `pipelines/README.md`, `CHANGELOG.md` only if the project regenerates it per release (otherwise leave)

**Concept:** documentation is part of done (plan.md, Documentation).

- [ ] **Step 1:** ADRs in the existing format (context, decision, alternatives, consequences), taken from spec section 3: Prefect vs Airflow vs APScheduler; layered raw plus normalised vs single pass vs queue workers; shared `db/` vs schema in `backend/`. Add them to the ADR index.
- [ ] **Step 2:** `docs/concepts/01-ingestion.md`: one section per **Concept** line in Tasks 1 to 10, and at least 12 interview questions with answers in English (idempotency, content-addressed storage, NULLS NOT DISTINCT, SSRF, MinHash, orchestration choice, replay from raw, why retries are safe, process isolation, rate limiting, ownership modelling, why no deletion of duplicates).
- [ ] **Step 3:** Package READMEs: purpose, setup, how to run, test, migrate, run a manual batch, the environment variables, the `integration` marker. Update `CLAUDE.md` Commands with the new test and migration commands and `plan.md` status.
- [ ] **Step 4:** Verify: every command written in a README or in `CLAUDE.md` was run once and works; `uv run pre-commit run --all-files` clean; links between docs resolve.
- [ ] **Step 5: Commit:** `docs: document the ingestion sub-project`.

---

## Self-review notes

- Spec coverage: layout (T1, T2, T9), data model (T2), components (T3 to T7), flow and idempotence (T7), error handling (T5, T7, T8), testing (each task, smoke in T9), threats (T3 SSRF and size cap, T5 isolation and timeout, T6 rate limit and user agent, T7 owner_id), evaluation criteria 1 to 4 (T7, T8, T9), criterion 5 (T10), criterion 6 (T9, T11).
- Spec refinement: `document_versions.minhash_signature` added (Task 2) so the near-duplicate index can be rebuilt across runs; the spec's table lists key columns only.
- Open dependency: Task 10 needs the user's review of the annotations; Task 9's optional `SMOKE_NETWORK=1` run needs outbound access to arXiv.
