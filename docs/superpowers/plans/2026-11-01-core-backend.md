# Core Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the backend a multi-user API: accounts, cookie sessions, private PDF uploads processed by the pipeline, and document reads, with isolation between users enforced both in repositories and by PostgreSQL Row-Level Security.

**Architecture:** The backend gains three layers: `api/` (routes and Pydantic schemas), `services/` (auth and document use cases), `repositories/` (SQL inside an identity-scoped session). Every database access of the API goes through `explorai_db.rls.scoped_session`, which switches to the `explorai_app` role and sets `app.user_id` for one transaction, so RLS policies apply. Sessions live in Redis. Uploads are written to the shared blob store and picked up by a new `process-pending` Prefect deployment.

**Tech Stack:** FastAPI (sync routes for business endpoints), SQLAlchemy 2 sync with psycopg 3, Alembic, PostgreSQL 16 RLS, redis-py (sync), argon2-cffi, email-validator, fakeredis (tests), Prefect 3, pytest.

**Spec:** `docs/superpowers/specs/2026-11-01-core-backend-design.md` (read it first). Conventions continue from `docs/superpowers/plans/2026-10-31-ingestion.md`.

## Global Constraints

- English in the repo; each task has a **Concept** line, turned into `docs/concepts/02-core-backend.md` in Task 10.
- GitFlow: work on `feature/core-backend` (spec already committed). Conventional Commits ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never push or change GitHub settings without the user's approval.
- Quality bar per package: `ruff`, `mypy --config-file <package>/pyproject.toml` (strict), `pytest` with the 80% gate, one call per package.
- All business routes under `/api/v1`; `/health/*` unchanged. Errors use FastAPI's `{"detail": ...}`.
- Status codes exactly as spec section 10. Someone else's document is always `404`; a public document on write is `403`.
- Passwords 12 to 128 characters, Argon2id. Session token `secrets.token_urlsafe(32)`, stored as `session:<sha256 hex>` in Redis; idle expiry 86400 s, absolute 604800 s. Cookie `explorai_session`, `HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` unless `SESSION_COOKIE_SECURE=false`.
- Login limits: 5 failures per email or 20 per IP within 900 s gives `429`. Upload limits: 20 per hour per user, 200 documents per user, 20 MB per file (`UPLOAD_MAX_BYTES=20000000`).
- No password, token, password hash or DSN in any response or log line.
- Tests never touch the network. Integration tests use a throwaway migrated database (`explorai_db.testing.temporary_database`) and Redis database 15, flushed per test.
- Plan-level refinements of the spec, recorded here so the executor does not re-decide them:
  1. **`SET LOCAL ROLE explorai_app` per transaction** (inside `scoped_session`) instead of `SET ROLE` per connection: the role and the identity both end with the transaction, so a pooled connection can never keep either.
  2. **RLS is `ENABLE`d without `FORCE`**: the API never runs as the table owner (it always switches role), while the pipeline, which runs as the owner and ingests for everyone, must keep bypassing RLS. An architecture test asserts that no backend module creates a session without `scoped_session`.
  3. **Upload is a raw body** (`POST /api/v1/documents?filename=paper.pdf`, `Content-Type: application/pdf`) instead of multipart: Starlette receives a multipart body completely before the handler runs, so a streamed size cap is only possible on a raw body.
  4. **An upload is idempotent per owner**: the document's `external_id` is the SHA-256 of the bytes, so uploading the same file twice returns the existing document with `200`.
  5. **Blobs are not deleted by the API.** Under RLS the API cannot see whether another owner's artifact shares a blob, so `process-pending` garbage-collects blobs referenced by no artifact and older than one hour (the grace period protects an upload whose rows are not committed yet).
  6. **Foreign keys change** in migration 0003: `document_versions.document_id` gets `ON DELETE CASCADE` and `document_versions.duplicate_of` gets `ON DELETE SET NULL`, so deleting a document never trips over versions of the same owner that marked it canonical.

## Review Focus

1. A pooled connection reused right after a scoped transaction: the next transaction sees neither the previous identity nor the `explorai_app` role. (Task 2)
2. A session whose idle TTL is still running but whose absolute age passed 7 days is rejected. (Task 4)
3. An upload body larger than the cap without a `Content-Length` header (chunked) is cut at the cap with `413`; an empty body is `415`. (Task 6)
4. Deleting a document whose version is the canonical of another document of the same owner succeeds and leaves the other document with `duplicate_of = NULL`. (Tasks 2, 6)
5. A garbage cookie, a cookie of a logged-out session, or Redis being down: `401`, `401`, `503`, never `500`. (Task 4)

---

### Task 1: Blob store moves to `db/`

**Files:**
- Create: `db/src/explorai_db/blobs.py`, `db/tests/unit/test_blobs.py`
- Delete: `pipelines/src/explorai_pipelines/storage.py`, `pipelines/tests/unit/test_storage.py`
- Modify: imports in `pipelines/src/explorai_pipelines/{ingest.py,flows/runtime.py}` and pipelines tests

**Interfaces:**
- Produces: `BlobStore` Protocol with `put(data: bytes) -> str`, `get(key: str) -> bytes`, `delete(key: str) -> None` (missing key is not an error), `keys_older_than(seconds: float) -> list[str]`; `LocalBlobStore(root: Path)` (same key rules as today).

**Concept:** a persistence layer shared by producers (API) and consumers (pipeline).

- [ ] **Step 1:** Move the existing storage tests into `db/tests/unit/test_blobs.py` and add failing tests: `test_delete_removes_blob`, `test_delete_missing_key_is_a_no_op`, `test_keys_older_than_skips_recent_blobs` (set an old mtime with `os.utime`).
- [ ] **Step 2:** Run `uv run pytest db/tests/unit/test_blobs.py --no-cov`; expect failures (module missing).
- [ ] **Step 3:** Create `blobs.py` from the old `storage.py` plus the two methods; update pipelines imports; delete the old files.
- [ ] **Step 4:** Run both packages' suites, ruff and mypy. Expected: green.
- [ ] **Step 5: Commit:** `refactor(db): move the blob store into the shared persistence package`.

---

### Task 2: Users, RLS and the scoped session (migration 0003)

**Files:**
- Modify: `db/src/explorai_db/models.py` (add `User`; `owner_id` foreign keys; FK actions of Global Constraint 6)
- Create: `db/src/explorai_db/migrations/versions/0003_users_and_rls.py`, `db/src/explorai_db/rls.py`
- Test: `db/tests/integration/test_rls.py`, extend `db/tests/integration/test_schema.py`

**Interfaces:**
- Produces: `User` model (`id`, `email`, `password_hash`, `created_at`, `is_active`, unique index on `lower(email)`); `APP_ROLE = "explorai_app"`; `scoped_session(factory: sessionmaker[Session], user_id: uuid.UUID | None) -> ContextManager[Session]` (begins a transaction, runs `SET LOCAL ROLE explorai_app` and `SELECT set_config('app.user_id', :uid, true)`, commits on success, rolls back on error); the upload source row (`kind='upload'`, `name='upload'`).
- Migration 0003, hand-written after autogenerate:
  - creates the role if missing (`DO $$ ... IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'explorai_app') THEN CREATE ROLE explorai_app NOLOGIN NOBYPASSRLS; END IF; $$`) and `GRANT explorai_app TO CURRENT_USER`;
  - creates `app_user() RETURNS uuid` as `SELECT NULLIF(current_setting('app.user_id', true), '')::uuid` (`STABLE`);
  - grants: `SELECT, INSERT` on `users`; `SELECT` on `sources`; `SELECT, INSERT, DELETE` on `documents`, `raw_artifacts`; `SELECT, DELETE` on `document_versions`, `sections`; nothing on `ingestion_runs`;
  - `ENABLE ROW LEVEL SECURITY` (no `FORCE`, Global Constraint 2) on `documents`, `raw_artifacts`, `document_versions`, `sections`, with policies `TO explorai_app`: documents and raw_artifacts `FOR SELECT USING (owner_id IS NULL OR owner_id = app_user())`, `FOR INSERT WITH CHECK (owner_id = app_user())`, `FOR DELETE USING (owner_id = app_user())`; document_versions `FOR SELECT USING (EXISTS (SELECT 1 FROM documents d WHERE d.id = document_id))` (the subquery is itself filtered by the documents policy) and `FOR DELETE USING (EXISTS (... AND d.owner_id = app_user()))`; sections likewise through `document_versions`;
  - inserts the upload source with `gen_random_uuid()` and `ON CONFLICT DO NOTHING`;
  - downgrade drops policies, the function, the grants, the FKs and `users`, but keeps the role (roles are cluster-wide and may hold grants in other databases).

**Concept:** Row-Level Security, roles, `SET LOCAL`, why the subquery inherits the policy.

- [ ] **Step 1: Write failing integration tests** in `test_rls.py`, with users A and B, a public document, and one private document each (inserted as the owner, outside RLS): `test_owner_sees_own_and_public_only` (raw `SELECT * FROM documents` in `scoped_session(A)` returns A's and the public row), `test_no_identity_sees_public_only`, `test_versions_and_sections_follow_their_document`, `test_cannot_insert_for_another_owner` (insert with `owner_id = B` inside A's scope raises), `test_cannot_delete_public_or_foreign_rows` (DELETE affects 0 rows), `test_identity_and_role_do_not_survive_the_transaction` (engine with `pool_size=1`: after A's scoped transaction, a plain session sees `current_user` = owner and `app_user()` IS NULL), `test_app_role_cannot_read_ingestion_runs`, `test_deleting_a_canonical_version_nulls_duplicate_of`. In `test_schema.py`: `test_email_unique_case_insensitive`, and keep `test_models_match_migrations` green.
- [ ] **Step 2:** Run with Postgres up; expect failures.
- [ ] **Step 3:** Implement the model changes, write the migration (autogenerate for the tables and FKs, then add the SQL above by hand with `op.execute`), implement `rls.py`.
- [ ] **Step 4:** `uv run pytest db/tests`, then `uv run pytest pipelines/tests` (the pipeline must still write as owner), ruff, mypy. Expected: green; also run `alembic downgrade 0002` and `upgrade head` once.
- [ ] **Step 5: Commit:** `feat(db): add users, row-level security and identity-scoped sessions`.

---

### Task 3: Backend configuration and wiring

**Files:**
- Modify: `backend/pyproject.toml` (dependencies `explorai-db` (workspace), `argon2-cffi`, `email-validator`; dev `fakeredis`), `backend/src/explorai_backend/config.py`, `main.py`
- Create: `backend/src/explorai_backend/deps.py`, `backend/src/explorai_backend/middleware.py`
- Test: `backend/tests/unit/test_config.py` (extend), `backend/tests/unit/test_origin_middleware.py`, `backend/tests/unit/test_architecture.py`

**Interfaces:**
- Produces: new `Settings` fields with the values of Global Constraints: `blob_dir: Path` (required), `upload_max_bytes`, `allowed_origins: list[str]` (comma-separated env value, default `http://127.0.0.1:5173`), `session_cookie_secure: bool = True`, `session_idle_seconds`, `session_max_seconds`, `login_max_failures_per_email`, `login_max_failures_per_ip`, `login_window_seconds`, `upload_max_per_hour`, `max_documents_per_user`; all positive and finite. `deps.py`: cached `get_session_factory() -> sessionmaker[Session]`, `get_redis() -> redis.Redis`, `get_blob_store() -> BlobStore`. `OriginCheckMiddleware`: on `POST|PUT|PATCH|DELETE`, a present `Origin` header not in `allowed_origins` gives `403 {"detail": "origin not allowed"}`; a missing `Origin` passes (non-browser clients).
- `test_architecture.py`: `test_no_backend_module_opens_sessions_outside_scoped_session` (scan `backend/src` for `sessionmaker(`, `Session(` and `.begin(`, allowed only in `deps.py`); `test_business_routes_are_versioned` (every non-health route path starts with `/api/v1`).

**Concept:** dependency injection of infrastructure; CSRF and the `Origin` header.

- [ ] **Step 1:** Failing tests: config defaults and validation (missing `BLOB_DIR` names the variable; `ALLOWED_ORIGINS="a,b"` parses to two entries), middleware (`test_foreign_origin_post_rejected`, `test_allowed_origin_post_passes`, `test_missing_origin_passes`, `test_get_is_never_checked`), architecture tests.
- [ ] **Step 2:** Run, expect failures. **Step 3:** Implement; update the existing health tests' environment fixtures with `BLOB_DIR`. **Step 4:** Backend suite, ruff, mypy green.
- [ ] **Step 5: Commit:** `feat(backend): add configuration, infrastructure dependencies and origin check`.

---

### Task 4: Authentication

**Files:**
- Create: `backend/src/explorai_backend/auth/{__init__.py,passwords.py,sessions.py,limits.py}`, `backend/src/explorai_backend/repositories/{__init__.py,users.py}`, `backend/src/explorai_backend/services/{__init__.py,auth.py}`, `backend/src/explorai_backend/api/auth.py`
- Test: `backend/tests/unit/test_passwords.py`, `test_sessions.py`, `test_limits.py`, `test_auth_service.py`; `backend/tests/integration/conftest.py`, `test_auth_api.py`

**Interfaces:**
- `passwords`: `hash_password(plain: str) -> str`, `verify_password(stored: str, plain: str) -> bool`, `verify_dummy(plain: str) -> None` (verifies against a module-level dummy hash; same cost).
- `sessions`: `SessionStore(redis: Redis, idle_seconds: int, max_seconds: int, clock: Callable[[], float] = time.time)` with `create(user_id: UUID) -> str` (returns the raw token), `resolve(token: str) -> UUID | None` (refreshes the idle TTL, rejects past the absolute age), `revoke(token: str) -> None`.
- `limits`: `FixedWindowLimiter(redis, prefix: str, limit: int, window_seconds: int)` with `hit(key: str) -> int`, `exceeded(key: str) -> bool`, `reset(key: str) -> None`.
- `repositories.users`: `UsersRepository(factory)` with `create(email: str, password_hash: str) -> UserRecord` (raises `EmailTaken`), `by_email(email: str) -> UserRecord | None`, `by_id(user_id: UUID) -> UserRecord | None`; all inside `scoped_session(factory, None)`. `UserRecord(id, email, password_hash, is_active)` frozen.
- `services.auth`: `AuthService(users, sessions, email_limiter, ip_limiter)` with `signup(email, password) -> PublicUser`, `login(email, password, ip) -> str` (raises `InvalidCredentials`, `TooManyAttempts`), `logout(token) -> None`, `user_for_token(token) -> PublicUser | None`.
- `api.auth`: routes of spec section 6 under `/api/v1/auth`; request models `Credentials(email: EmailStr, password: str = Field(min_length=12, max_length=128))`; response `PublicUser(id: UUID, email: str)`; `current_user` dependency (`401` without or with an unknown cookie, `503` on `redis.RedisError`). The login response sets the cookie; logout clears it.

**Concept:** password hashing (salt, memory-hard), opaque sessions, constant-time verification, fixed-window rate limiting, session fixation.

- [ ] **Step 1: Failing unit tests** (fakeredis, fake clock): `test_hash_is_argon2id_and_salted` (two hashes of one password differ, both verify), `test_wrong_password_fails`, `test_session_token_is_stored_hashed` (no Redis key contains the raw token), `test_idle_expiry`, `test_absolute_expiry_beats_idle_refresh` (Review Focus 2), `test_revoke`, `test_limiter_window`, `test_login_unknown_email_still_verifies_a_hash` (spy on `verify_dummy`), `test_login_is_limited_per_email_and_per_ip`, `test_successful_login_resets_the_email_counter`, `test_email_is_case_insensitive`.
- [ ] **Step 2: Failing integration tests** (`TestClient`, temporary database, Redis db 15): `test_signup_login_me_logout`, `test_signup_duplicate_email_is_409`, `test_short_or_long_password_is_422`, `test_login_sets_httponly_samesite_cookie`, `test_new_login_issues_a_new_token`, `test_garbage_cookie_is_401`, `test_logged_out_cookie_is_401`, `test_redis_down_is_503` (override `get_redis` with a client on a closed port), `test_no_secret_in_responses_or_logs` (password, token and hash absent from every response body and from `caplog`).
- [ ] **Step 3:** Run, expect failures. **Step 4:** Implement. **Step 5:** Backend suite (unit and integration) green, ruff, mypy.
- [ ] **Step 6: Commit:** `feat(backend): add accounts, password login and cookie sessions`.

---

### Task 5: Reading documents

**Files:**
- Create: `backend/src/explorai_backend/repositories/documents.py`, `backend/src/explorai_backend/services/documents.py`, `backend/src/explorai_backend/api/documents.py`, `backend/src/explorai_backend/api/pagination.py`
- Test: `backend/tests/unit/test_pagination.py`, `backend/tests/integration/test_documents_read.py`

**Interfaces:**
- `pagination`: `encode_cursor(created_at: datetime, id: UUID) -> str`, `decode_cursor(cursor: str) -> tuple[datetime, UUID]` (raises `ValueError` on garbage, mapped to `422`).
- `DocumentsRepository(factory)` with `list(user_id, scope: Literal["mine","public","all"], limit: int, after: tuple[datetime, UUID] | None) -> list[DocumentRow]`, `get(user_id, document_id) -> DocumentRow | None`, `sections(user_id, document_id, limit, after_ordinal: int | None) -> list[SectionRow] | None` (None if no `ok` version). Queries filter explicitly by owner **and** run in `scoped_session(factory, user_id)`. `DocumentRow(id, title, source_kind, owned: bool, status: Literal["processing","ready","failed"], created_at)`; status from the latest version by `created_at` (none: processing; `ok`: ready; `unextractable`: failed). Order: `created_at DESC, id DESC`.
- Routes of spec section 7 (GET ones), response models `DocumentOut`, `DocumentPage(items, next_cursor: str | None)`, `SectionOut(ordinal, heading, text)`, `SectionPage`. `limit` between 1 and 100, default 20.

**Concept:** keyset pagination, why 404 instead of 403, computed status.

- [ ] **Step 1: Failing tests:** cursor round trip and garbage cursor; integration: `test_list_scopes` (mine, public, all), `test_pagination_is_stable_when_documents_are_added`, `test_status_processing_ready_failed`, `test_sections_of_processing_document_is_409`, `test_unknown_id_is_404`, `test_malformed_id_is_422`, `test_routes_require_login` (401).
- [ ] **Step 2:** Run, expect failures. **Step 3:** Implement. **Step 4:** Suite, ruff, mypy green.
- [ ] **Step 5: Commit:** `feat(backend): list and read documents with keyset pagination`.

---

### Task 6: Upload and delete

**Files:**
- Create: `backend/src/explorai_backend/uploads.py`
- Modify: `repositories/documents.py`, `services/documents.py`, `api/documents.py`
- Test: `backend/tests/unit/test_uploads.py`, `backend/tests/integration/test_documents_write.py`

**Interfaces:**
- `uploads`: `read_capped(chunks: AsyncIterator[bytes], max_bytes: int) -> bytes` (raises `TooLarge` as soon as the total passes the cap), `require_pdf(data: bytes) -> None` (raises `NotPdf` unless it starts with `%PDF-`; empty is `NotPdf`), `clean_title(filename: str | None) -> str` (basename only, no extension, control characters removed, whitespace collapsed, at most 200 characters, `"Untitled document"` if empty).
- `DocumentsRepository.create_upload(user_id, sha256, storage_key, title) -> tuple[DocumentRow, bool created]` (one scoped transaction: artifact and document with `owner_id = user_id`, `source = upload`, `external_id = sha256`; returns the existing document when the same owner already uploaded these bytes); `count_owned(user_id) -> int`; `delete(user_id, document_id) -> Literal["deleted","not_found","read_only"]` (deletes the document, which cascades to versions and sections, then the artifacts of that document identity; never touches blobs).
- `POST /api/v1/documents?filename=` reads `request.stream()` through `read_capped` (an async route; the blob write and repository calls run with `run_in_threadpool`), checks the hourly limiter and the quota, writes the blob, then the rows. Answers: `202` new, `200` already uploaded, `413`, `415`, `429`, `409` quota. `DELETE /api/v1/documents/{id}`: `204`, `404`, `403`.

**Concept:** streaming request bodies, magic bytes, idempotent creation, quotas.

- [ ] **Step 1: Failing unit tests:** `test_read_capped_stops_at_the_cap` (async generator counting consumed chunks), `test_empty_body_is_not_pdf`, `test_html_body_is_not_pdf`, `test_clean_title_strips_paths_and_controls` (`"../../etc/passwd"`, `"C:\\x\\report.pdf"`, control characters).
- [ ] **Step 2: Failing integration tests:** `test_upload_returns_202_and_processing`, `test_same_file_twice_returns_200_same_id`, `test_chunked_body_over_cap_is_413` (Review Focus 3: send a generator body with no `Content-Length`), `test_empty_body_is_415`, `test_non_pdf_is_415`, `test_hourly_upload_limit_is_429`, `test_document_quota_is_409`, `test_delete_own_document` (rows gone, blob still present), `test_delete_public_is_403`, `test_delete_canonical_keeps_duplicate_with_null_pointer` (Review Focus 4), `test_foreign_origin_upload_is_403`.
- [ ] **Step 3:** Run, expect failures. **Step 4:** Implement. **Step 5:** Suite, ruff, mypy green.
- [ ] **Step 6: Commit:** `feat(backend): upload private PDFs and delete documents`.

---

### Task 7: Isolation suite (the success criterion)

**Files:**
- Create: `backend/tests/integration/test_isolation.py`

**Interfaces:** consumes the routes of Tasks 4 to 6 and `explorai_db.rls`.

**Concept:** testing an invariant across every route instead of route by route.

- [ ] **Step 1: Write the suite.** Fixture: users A and B logged in (two `TestClient`s with their own cookies), B owns a ready document (inserted with a version and sections as owner), a public ready document exists. Tests:
  - `test_every_document_route_hides_foreign_documents`: iterate over `app.routes` whose path starts with `/api/v1/documents`; for routes with `{id}`, call them as A with B's id: GET answers `404`, DELETE answers `404` and B's document still exists; for the list route, call each scope and assert B's id never appears across all pages.
  - `test_every_business_route_requires_login`: every `/api/v1` route except signup and login answers `401` without a cookie.
  - `test_guessing_ids_reveals_nothing`: B's id and a random UUID give byte-identical `404` bodies for A.
  - `test_rls_holds_even_without_the_repository_filter`: run `SELECT id FROM documents` (no WHERE) through `scoped_session(factory, A)` and assert B's id is absent.
  - `test_new_routes_are_covered`: assert the set of document routes found equals the expected set, so adding a route forces updating this suite.
- [ ] **Step 2:** Run. These tests should pass on the implementation of Tasks 4 to 6; to prove they can fail, temporarily remove the owner filter in `DocumentsRepository.get` **and** run the route under the owner role (bypassing `scoped_session`) in a scratch change, watch `test_every_document_route_hides_foreign_documents` fail, then revert. Record that in the commit body.
- [ ] **Step 3: Commit:** `test(backend): prove user isolation on every document route`.

---

### Task 8: Pipelines: process uploads, lock, collect blobs

**Files:**
- Modify: `pipelines/src/explorai_pipelines/{ingest.py,repository.py,__main__.py}`
- Create: `pipelines/src/explorai_pipelines/flows/process_pending.py`
- Test: `pipelines/tests/integration/test_process_pending.py`, extend `test_flows.py`, `test_cli.py`

**Interfaces:**
- `Repository.advisory_lock(key: int) -> ContextManager[bool]` (holds a dedicated connection with `pg_try_advisory_lock`; yields whether it got the lock; unlocks on exit). `CATCH_UP_LOCK = 4242001` constant in `ingest.py`; `reextract` takes the lock and returns all-zero counters plus `"skipped": 1` when another process holds it.
- `Repository.referenced_storage_keys() -> set[str]`; `collect_blobs(repo, store, grace_seconds: float = 3600) -> int` in `ingest.py` (deletes blobs older than the grace period that no artifact references; returns the count).
- `process_pending_flow() -> dict[str, int]` (`@flow(name="process-pending")`): `reextract` with `DEFAULT_EXTRACTORS`, then `collect_blobs`; counters plus `"blobs_deleted"`.
- `__main__.serve()` serves two deployments with `prefect.serve(...)`: `nightly-arxiv` (cron as today) and `process-pending` (`interval=30`). New CLI command `process-pending`.

**Concept:** advisory locks, garbage collection with a grace period.

- [ ] **Step 1: Failing tests:** `test_uploaded_artifact_is_processed_into_the_existing_document` (artifact and document inserted as an upload; the flow adds a version; the document keeps its title), `test_second_runner_is_skipped_while_the_lock_is_held`, `test_unreferenced_old_blob_is_collected`, `test_recent_or_referenced_blob_is_kept`, CLI parse of `process-pending`.
- [ ] **Step 2:** Run, expect failures. **Step 3:** Implement. **Step 4:** Pipelines suite, ruff, mypy green.
- [ ] **Step 5: Commit:** `feat(pipelines): process uploads every 30 seconds under an advisory lock`.

---

### Task 9: Contract, Compose, smoke and CI

**Files:**
- Modify: `backend/openapi.json`, `frontend/src/api/schema.d.ts` (regenerated), `backend/tests/unit/test_openapi_contract.py` (assert the new paths exist), `frontend/vite.config.ts` (proxy `/api`), `backend/Dockerfile` (create `/data/blobs` owned by uid 10001), `infra/docker-compose.yml` (backend: `BLOB_DIR=/data/blobs`, `ALLOWED_ORIGINS`, the `blobs` volume, `depends_on` migrate completed), `.env.example`, `infra/smoke.sh`, `.github/workflows/ci.yml` (backend job env: `BLOB_DIR`, `ALLOWED_ORIGINS`)

**Concept:** contract-first integration; an end-to-end test of the user journey.

- [ ] **Step 1:** Extend `smoke.sh` first: sign up and log in with `curl -c/-b` and an `Origin: http://127.0.0.1:5173` header, upload `pipelines/tests/fixtures/annotated/2610.40627.pdf`, poll `GET /api/v1/documents/{id}` up to 180 s until `ready`, then `GET .../sections` answers `200`; also check that a second user gets `404` on that id. Run it, watch it fail.
- [ ] **Step 2:** Regenerate the contract (`export_openapi.py`, `pnpm --dir frontend gen:api`), update Compose, Dockerfile, env files and CI.
- [ ] **Step 3:** `bash infra/smoke.sh` until all checks pass; run the CI commands locally (three Python packages, frontend lint, typecheck, test, build, contract diff).
- [ ] **Step 4: Commit:** `feat(infra): serve the user API end to end and extend the smoke test`.

---

### Task 10: Documentation

**Files:**
- Create: `docs/adr/0009-opaque-sessions-over-jwt.md`, `docs/adr/0010-row-level-security-defence-in-depth.md`, `docs/concepts/02-core-backend.md`
- Modify: `docs/adr/0008-shared-db-package.md` (status note: amended, `db/` is the persistence layer including blobs), `docs/adr/README.md`, `backend/README.md`, `db/README.md`, `pipelines/README.md`, `README.md`, `CLAUDE.md`, `plan.md`, the spec (a "Refinements during planning" section listing Global Constraints 1 to 6)

**Concept:** documentation is part of done.

- [ ] **Step 1:** ADRs from spec section 3 and the refinements (why `SET LOCAL ROLE`, why no `FORCE`).
- [ ] **Step 2:** Concepts note: one section per **Concept** line, at least 14 interview questions with answers (IDOR, RLS vs app filters, opaque session vs JWT, HttpOnly/Secure/SameSite, CSRF and `Origin`, Argon2id and salts, constant-time login, rate limiting, keyset pagination, 404 vs 403, streaming uploads and magic bytes, idempotent uploads, advisory locks, blob GC grace period, testing an invariant across all routes).
- [ ] **Step 3:** READMEs: API routes with `curl` examples (cookie jar), new variables, how to run the isolation suite. `CLAUDE.md` commands and current state; `plan.md` status.
- [ ] **Step 4:** Run every new documented command once, check relative links, `pre-commit run --all-files`.
- [ ] **Step 5: Commit:** `docs: document the core backend sub-project`.

---

## Self-review notes

- Spec coverage: data model (T2), RLS (T2, T7), auth and sessions (T4), routes (T5, T6), upload flow (T6, T8), configuration (T3, T9), errors (T4 to T6), testing incl. isolation and RLS net (T2, T7), threats (T3 origin, T4 brute force and enumeration, T6 upload, T7 IDOR), evaluation criteria 1 to 6 (T6, T7, T4, T6, T9, T10).
- Refinements of the spec are listed once in Global Constraints and copied into the spec in Task 10.
- Open dependencies: Task 9's smoke waits for the 30-second deployment; allow up to 180 s on a cold stack.
