# Ingestion: concepts and interview questions

Concept notes for sub-project 1. Each concept is explained plainly, then tied to the files where it lives. The questions at the end are practice for an English interview; the answers are short on purpose.

## 1. A shared database package in a uv workspace

**Idea.** A workspace is several Python packages developed together with one lockfile. When two packages need the same tables, the schema gets its own package so neither depends on the other.

**In this repo.** `db/` (`explorai_db`) holds the models and migrations; `pipelines/` depends on it through `[tool.uv.sources] explorai-db = { workspace = true }`. Each package has its own pytest and mypy configuration, so they are run once per package (a combined run picks the root configuration). See ADR 0008.

## 2. Schema migrations and the `NULL` trap

**Idea.** A migration is a versioned, reviewable change to the database schema. Alembic records which migrations ran, and can generate one by comparing the models with the database.

**The trap.** In SQL, `NULL` is "unknown", so two `NULL`s are never equal. A unique constraint on `(source, external_id, owner_id)` therefore accepts the same public document twice when `owner_id` is `NULL`. PostgreSQL 15+ has `UNIQUE NULLS NOT DISTINCT`, which treats `NULL`s as equal. Without it, the shared public corpus would have no uniqueness at all.

**In this repo.** `db/src/explorai_db/models.py` sets `postgresql_nulls_not_distinct=True` on the two unique constraints involving `owner_id`; `db/tests/integration/test_schema.py` proves it, and `test_models_match_migrations` fails if models and migration drift.

## 3. Content-addressed storage

**Idea.** The key of a blob is the hash of its bytes. Storing the same bytes twice gives the same key, so writes are idempotent for free, and a key proves what the content is.

**In this repo.** `storage.py`: `LocalBlobStore.put` returns the SHA-256 hex digest, writes to a temporary file and renames it (a crash never leaves a truncated blob under a valid key), and `get` rejects any key that is not 64 lowercase hex characters, which also blocks path traversal.

## 4. Safe fetching: SSRF and the size cap

**Idea.** SSRF (server-side request forgery) means tricking a server into requesting an internal address, such as a cloud metadata endpoint or `localhost`. A downloader that follows URLs from untrusted data is a target. A hostile server can also stream gigabytes to exhaust memory.

**In this repo.** `netsafe.fetch_bytes`: HTTPS only, a per-source domain allowlist (exact domain or subdomain, so `notarxiv.org` fails), every resolved address must be globally routable, and redirects are followed by hand so **each hop** is re-validated. The body is read in chunks and aborted as soon as it passes `MAX_DOWNLOAD_BYTES`; a declared `Content-Length` above the cap is refused before reading anything. Known limit: the check resolves the name and the HTTP client resolves it again, so a DNS rebinding attack is not fully closed; pinning the resolved address would close it.

## 5. Why PDF text extraction is hard

**Idea.** A PDF does not store paragraphs. It stores drawing instructions: place this string at these coordinates. Reading order, columns, running headers and hyphenated line ends must be reconstructed.

**In this repo.** `extract/pdf.py` reads PyMuPDF text blocks, puts blocks in the left column before the right one (a block spanning the middle flushes the columns), removes lines repeated at page tops and bottoms (`clean.remove_repeated_lines`), joins words split by a hyphen at a line end, and splits sections on heading-like lines. A PDF with no text layer (a scan) is `unextractable`; there is no OCR in this sub-project. `docs/evaluation/extraction-baseline.md` measures how well it works and where it fails.

## 6. Process isolation as a guardrail

**Idea.** A parser written in C that reads untrusted files can hang or crash. Running it in a separate process with a timeout turns "the pipeline froze" into "this document failed".

**In this repo.** `extract_pdf_isolated` starts a spawned process (spawn, not fork, so it behaves the same on Windows and Linux), waits up to `EXTRACT_TIMEOUT_SECONDS`, kills it on timeout, and converts a crash or a timeout into `Unextractable`. The container also runs as a non-root user.

## 7. Near-duplicate detection with MinHash

**Idea.** Two documents are near-duplicates if their sets of word 5-grams ("shingles") overlap a lot. The Jaccard index is `|A ∩ B| / |A ∪ B|`. MinHash compresses a set into a short signature so that the fraction of equal slots estimates the Jaccard index.

**In this repo.** `dedup.py`: `minhash_signature` (128 slots), `NearDuplicateIndex` (threshold 0.9, a linear scan of stored signatures). Changing one word in 200 changes about 5 shingles of 196, a Jaccard of about 0.95. Duplicates are **marked, not deleted** (`duplicate_of` points at a canonical version), because a deletion cannot be undone and a threshold can be wrong. Only canonical versions are indexed, so duplicates never form chains. The scalable alternative is locality-sensitive hashing (LSH); it was tried and rejected here because it is probabilistic around its threshold and missed a 0.945 pair at threshold 0.9.

## 8. Idempotency and transactions

**Idempotent** means running an operation twice has the same effect as running it once. Pipelines fail and get retried, so every step must be idempotent.

**In this repo.** An artifact is identified by `(source, external_id, owner, sha256)` and a version by `(artifact, extractor_version)`. `ingest_item` returns `unchanged` only when the artifact already has a version for the current extractor. One transaction saves a document, its version and its sections, so a failure leaves nothing partial; an unexpected error stores no version, so the next run retries it, while an `unextractable` verdict is stored because it will not change until the extractor does. `test_replaying_same_batch_changes_nothing` is the success criterion of the sub-project.

## 9. Replaying from the raw layer

**Idea.** Keeping the original input lets you recompute outputs when the algorithm improves, and compare generations.

**In this repo.** `ingest.reextract` takes artifacts that lack a version for an extractor and creates one from stored bytes, with no network (`test_reextract_with_new_extractor_version_needs_no_network`). See ADR 0007.

## 10. Orchestration: flow, task, retry, schedule

**Idea.** An orchestrator runs steps in order, retries failures, keeps a history and starts runs on a schedule. In Prefect a **flow** is the whole run, a **task** a unit inside it that can retry independently.

**In this repo.** `flows/` wire the pure functions of `ingest.py`. Network tasks retry three times with growing delays, but only for transient errors (`flows/common.is_transient`: transport errors, HTTP 5xx and 429). A 404 or a refused URL fails the same way next time. Retrying is safe because every step is idempotent. See ADR 0006.

## 11. Polite crawling

**Idea.** Sources publish usage rules. arXiv asks for sequential requests, a pause of about three seconds, and an identifiable `User-Agent`.

**In this repo.** `ArxivSource` spaces requests with an injectable clock (so tests are instant), sends `explorai-ingestion (mailto:<CONTACT_EMAIL>)`, and uses the official Atom API, not scraping.

## 12. Measure before you improve

**Idea.** An improvement without a baseline is a feeling. Annotating a small sample by hand gives numbers that later changes must beat.

**In this repo.** `python -m explorai_pipelines.measure` scores the annotated sample: share of fragment pairs in the right order, and share of running headers removed. The baseline is 12/12 pairs and 2/4 headers, and the failure causes are written down as the to-do list of sub-project 11.

## 13. Why the test stack does not call the network

CI must not depend on a third-party API: it would fail when arXiv is slow and it would hammer a shared service on every pull request. Unit and integration tests use `httpx.MockTransport` and fixture feeds. The Compose smoke test only checks the services and the schema; a real two-paper batch runs on demand with `SMOKE_NETWORK=1 bash infra/smoke.sh`.

## Interview questions

**1. What does idempotent mean, and how does your pipeline achieve it?**
Running it twice gives the same result as once. Artifacts are keyed by `(source, external id, owner, hash)` and versions by `(artifact, extractor version)`; a replay finds them and writes nothing. A test runs the same batch twice and compares the table contents.

**2. Why keep the raw bytes instead of only the extracted text?**
So that extraction can be redone and compared when the extractor improves, without downloading again (arXiv rate-limits) and with a reproducible result. The cost is storage.

**3. What is content-addressed storage?**
The key is the hash of the content. The same bytes give the same key, so duplicate writes are harmless, and the key identifies the content.

**4. What is SSRF and how do you defend against it?**
Tricking the server into requesting internal addresses. I allow only HTTPS to listed domains, refuse addresses that are not globally routable, and re-validate every redirect hop. A remaining gap is DNS rebinding between my check and the client's own lookup.

**5. Why `NULLS NOT DISTINCT`?**
In SQL two `NULL`s are not equal, so a unique constraint on a nullable column does not stop duplicates. Public documents have a `NULL` owner, so without it the shared corpus could hold the same document twice.

**6. How do you stop a bad PDF from taking the pipeline down?**
Extraction runs in a separate process with a timeout; a hang or a crash becomes an `unextractable` document and the batch continues. There is also a size cap on download and a page limit.

**7. What is MinHash and why not just compare texts?**
It compresses a set of shingles into a short signature whose equal-slot fraction estimates the Jaccard similarity. Comparing full texts pairwise is quadratic and brittle to small edits.

**8. Why mark duplicates instead of deleting them?**
A threshold can be wrong, and a deletion is irreversible. A pointer to the canonical version keeps the data and lets the consumer choose.

**9. Why Prefect and not Airflow?**
Airflow needs a scheduler, a web server and a metadata database, which is heavy for this need, and the concepts transfer (flow to DAG, task to task). Prefect is plain Python with retries, schedules and a UI, cheap to run in Compose.

**10. Why is retrying safe in your flows, and which errors do you retry?**
Because each step is idempotent. I retry transport errors, 5xx and 429; a 404 or a refused URL would fail again, so retrying only wastes the rate limit.

**11. Why does the business logic not live inside Prefect tasks?**
So it can be tested without a server and does not depend on the orchestrator. Flows are thin wiring; `ingest.py` is plain functions.

**12. Why a separate `db/` package?**
The pipelines and the backend need the same tables; putting the schema in the backend would make batch code depend on the API. One package owns the models and the single migration history.

**13. How do you know your extraction is any good?**
I measure it on a hand-annotated sample: column order (12 of 12 pairs) and header removal (2 of 4). The weak spot is documented with its causes, and the number becomes the baseline for improvement.

**14. What happens when one document in a batch fails?**
The item is recorded as failed in `ingestion_runs` and the batch continues. A transaction per document means nothing partial is written, and an unexpected failure stores no version, so the next run retries it.

**15. Why does CI not download from arXiv?**
It would couple the build to a third-party service and hit it on every pull request. Tests use mocked transports; a real batch is an opt-in smoke check.
