# Core backend: deferred findings

Minor findings from the per-task and final reviews of sub-project 2 that were deferred on purpose. None blocks the merge; each is a candidate for a later change. Findings that were fixed or documented during the branch are not listed. They are recorded here because the review workspace is deleted when the branch is finished.

## Security and robustness

- `ScopedSessions` is a convention guard: its name-mangled attribute is still readable, and a route could declare `Depends(get_session_factory)` (a reference, not a call, so the architecture test does not flag it). Forbid references to `get_session_factory` outside `deps.py`.
- The architecture test does not catch assignment aliases (`f = make_engine; f()`), and `test_only_the_exact_deps_file_is_exempt` does not exercise the exemption logic.
- Login limiter keys contain the email in clear; hash them like the session keys.
- The per-IP login key is one exact address (an IPv6 client can rotate within a /64); a future reverse proxy needs a trusted-proxy setting.
- Python and PostgreSQL lowercase some non-ASCII emails differently, so such an account could fail to match at login.
- The limiter's `release` is dropped on a Redis `WatchError` (errs on the strict side).
- No `Retry-After` header on 429 answers.
- `delete_if_older_than` has no try/except restore after the rename: an `OSError` there leaves an aside file and the blob missing until the next `put`. `collect_blobs` aborts the whole sweep on one `OSError` (the next run retries).
- If `pg_advisory_unlock` fails, the pooled connection keeps the session lock; invalidate the connection in that path.
- Artifacts whose bytes are neither PDF nor HTML never get a version and are re-read by every `process-pending` pass.
- `read_capped` copies the buffer into `bytes`, doubling peak memory per upload (about 800 MB worst case with 20 uploads in flight).
- A `get_settings()` failure inside the Origin middleware gives a bare 500; the middleware also bypasses `dependency_overrides`.
- No validation that `session_max_seconds >= session_idle_seconds`; `allowed_origins` entries are not normalised.

## API polish

- The DELETE route's OpenAPI lacks 401 and 503; the upload's 202 keeps a generic description; the backend README route table omits 401 and the Origin 403.
- `clean_title` turns `..` into `"."` and strips ZWJ/ZWNJ characters.
- The generated client types the PDF body as `string` (sub-project 6 needs a Blob serializer); 200/202 return the full `DocumentOut`, a superset of the spec's `{id, status}`.
- The service's `sections()` opens two sessions.
- Bare `assert` statements guard invariants in `main.py` (422 handler) and `repositories/documents.py` (`create_upload`).

## Database

- Policy subqueries use unqualified `document_id` / `version_id`.
- The 0003 downgrade keeps the upload source row.

## Tests

- No test that `UPDATE` is denied to `explorai_app`, or that `scoped_session` rolls back data on error.
- Test engines are never disposed (`test_rls.py`, `test_deps.py`); `test_deps` leaves cached settings behind.
- Config tests do not clear every new variable (spurious failures with `infra/.env` exported).
- `documents.created_at` server default is not exercised (the ORM fills the Python default); the backfill test is weak (no non-NULL or mismatched owner case).
- The explicit owner filter is masked by RLS in tests (no RLS-bypassing repository test).
- Upload: release on 413 and 409 untested; the two-owners idempotency test does not check each owner's artifact row.
- Isolation suite: `startswith(DOCUMENTS)` would match a sibling path; the login sweep uses a loose floor; `set(seen)` ignores duplicate ids across pages.
- Forged-cursor integration test relies on the cursor being validated before the document lookup.
- No test that blob collection still runs when the catch-up is skipped; the deployments' interval and cron are untested.
- `Repository.advisory_lock` reads sessionmaker internals (`factory.kw["bind"]`).
- Inaccurate comments in `test_auth_api.py` and `test_sessions.py`; an untyped fixture in `test_origin_middleware.py`; a possibly unused `type: ignore` in `test_scoped_sessions.py`; a redundant assertion in `test_keys_older_than`.
- `PytestDeprecationWarning` about `asyncio_default_fixture_loop_scope` in test output.
- Smoke script: uses `seq`; does not re-check the first user's access after the second user's 404.

## Runtime checks not yet run on this branch

- `bash infra/smoke.sh` and the integration suites of `backend`, `db` and `pipelines` were not run against the final code: Docker Desktop was unresponsive on the development machine. CI runs all of them on the pull request.
