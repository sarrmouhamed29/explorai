# Sub-project 2: Core backend, design spec

Part of the master plan (`plan.md`). Date: 2026-11-01. Status: draft, awaiting review.

## 1. Goal

Turn the backend into a real, multi-user API. A user creates an account, logs in, uploads private PDFs that the existing ingestion pipeline processes, and reads their own documents and the shared public corpus. Isolation between users is the core guarantee: **a user can never read, list or delete another user's data**, and an automated test proves it for every document route.

## 2. Scope

**In scope**
- Users table, password authentication, server-side sessions in an HttpOnly cookie.
- Document routes: list, read, read sections, upload (PDF), delete.
- Isolation in two layers: explicit owner filtering in repositories, and PostgreSQL Row-Level Security (RLS) as a safety net.
- Upload processing through a durable queue in the database, picked up by a new pipelines deployment every 30 seconds.
- Moving `BlobStore` from `pipelines/` into `db/`, so the API writes uploads without depending on the pipelines package.
- OpenAPI contract and regenerated TypeScript client, API tests, documentation.

**Out of scope**
- Frontend pages (login, upload, document views): sub-project 6.
- Password reset, email confirmation, MFA, "log out everywhere".
- Sharing documents between users, collections, roles.
- Search, chunking, answers: sub-project 3.
- A separate database login role with its own password, TLS, production deployment: sub-project 10.
- A retry cap for uploads that keep failing transiently.

## 3. Decisions and rejected alternatives

| Topic | Decision | Alternatives not chosen |
|---|---|---|
| User features | Account, private PDF upload, list/read/delete own documents, read the public corpus | Read-only (isolation tested on artificial data); collections and sharing (authorization becomes a sub-project on its own) |
| Authentication | Argon2id password hashes; opaque random session token, stored hashed in Redis, sent in an HttpOnly cookie | Short JWT plus refresh token (more moving parts, a stolen JWT stays valid until expiry, token storage in the browser); external OIDC provider such as Keycloak (another heavy service, the mechanisms are delegated rather than learned) |
| Isolation | Both: repository filters by owner, and RLS policies enforced by PostgreSQL | Application only (one forgotten filter leaks); RLS only (a role misconfiguration silently disables everything, queries less explicit) |
| Upload processing | Durable queue in the database: the API stores bytes and rows and answers 202; a Prefect deployment every 30 s processes artifacts without a version | API triggers a Prefect run (couples the API to Prefect's availability); synchronous extraction in the request (long requests, hostile PDFs handled by the web service) |
| Backend structure | Layers: routes, services (use cases), repositories; persistence (database and blob store) in the shared `db/` package | Routes calling repositories directly (rules end up in routes); FastAPI-Users (its schema and strategies dictate the design and fit RLS poorly, and the mechanisms are not learned) |

ADRs: opaque sessions over JWT; defence in depth with RLS; `db/` as the shared persistence layer (amends ADR 0008).

## 4. Data model (migration 0003)

| Change | Content |
|---|---|
| `users` table | `id` (UUID), `email` (unique on its lowercase form), `password_hash` (Argon2id), `created_at`, `is_active` |
| `owner_id` | becomes a foreign key to `users.id` on `documents` and `raw_artifacts` |
| upload source | one `sources` row with `kind = "upload"` |

A document's **status** is computed, with no new column: no version yet means `processing`; latest version `ok` means `ready`; latest version `unextractable` means `failed`.

An upload creates the document immediately (title from the cleaned file name) so that it appears in the user's list at once; the pipeline later adds the version to that existing document.

**Deleting a document** removes the document, its versions, its sections and its artifacts in one transaction. The blob is deleted only when no other artifact references the same hash, because storage is content-addressed.

## 5. Row-Level Security

- **Roles.** The table owner runs migrations and the pipeline, which ingests for everyone. A role `explorai_app`, without `BYPASSRLS`, is used by the API: every API connection runs `SET ROLE explorai_app`.
- **Identity per transaction.** Each API transaction starts with `set_config('app.user_id', '<uuid>', true)`. The setting is local to the transaction, so a pooled connection never keeps the previous user's identity. Without it, only public rows are visible.
- **Policies**, with `ENABLE` and `FORCE ROW LEVEL SECURITY`, on `documents`, `raw_artifacts`, `document_versions` and `sections`:
  - read: `owner_id IS NULL OR owner_id = current user`; versions and sections follow their document through a subquery;
  - insert, update, delete: only when `owner_id = current user`. The public corpus is read-only for the API.
- **`users` is not under RLS.** Login must look up an email before the user is known. Only the authentication repository reads it, and password hashes are never returned by the API. `explorai_app` has no privileges on `ingestion_runs`.

## 6. Authentication and sessions

**Sign-up (`POST /api/v1/auth/signup`).** The email is validated and lowercased. The password must be 12 to 128 characters, with no composition rules (NIST SP 800-63B: length matters more than character classes; the upper bound stops very long inputs from being used to load the deliberately slow hash). Argon2id with a per-password salt. An email already in use gives `409`; this reveals that an account exists, which is a documented limitation without email confirmation.

**Login (`POST /api/v1/auth/login`).**
- One answer for every failure ("invalid credentials"). When the email is unknown, a dummy hash is still verified, so response time does not reveal which emails exist.
- Brute-force limit: after 5 failures in 15 minutes for one email, or 20 for one IP address, the answer is `429`. Counters live in Redis.
- Session token: `secrets.token_urlsafe(32)` (256 bits), new on every login (no session fixation). Redis stores `session:<sha256(token)>` with the user id and the creation time; only the hash is stored, so a Redis leak yields no usable token. The session expires after 24 hours without activity (renewed on use) and at most 7 days after login.
- Cookie `explorai_session`: `HttpOnly`, `Secure` (configurable for plain-HTTP setups), `SameSite=Lax`, `Path=/`.

**Other routes.** `POST /api/v1/auth/logout` deletes the session server-side. `GET /api/v1/auth/me` returns the current user.

**CSRF.** Two layers: `SameSite=Lax`, and a middleware that rejects `POST`, `PUT`, `PATCH` and `DELETE` requests whose `Origin` header is not in `ALLOWED_ORIGINS`.

**`current_user` dependency.** It reads the cookie, checks the session in Redis and returns the user, or `401`. Every document route requires it. If Redis is unavailable the answer is `503`: the API fails closed.

## 7. API routes

All business routes are under `/api/v1`; `/health/*` is unchanged.

| Route | Purpose | Answers |
|---|---|---|
| `GET /documents?scope=mine\|public\|all&limit=&cursor=` | paginated list: title, source kind, status, owned or public, creation date | 200 |
| `GET /documents/{id}` | metadata and status | 200; 404 if missing **or** owned by someone else |
| `GET /documents/{id}/sections?limit=&cursor=` | sections of the latest `ok` version | 200; 404; 409 while still processing |
| `POST /documents` (multipart) | upload a private PDF | 202 with `{id, status: "processing"}` |
| `DELETE /documents/{id}` | delete one of your documents | 204; 404; 403 for a public document |

- **404, not 403, for someone else's document**: a 403 would confirm the id exists.
- **Cursor pagination** (last creation date and id seen) instead of offsets: stable while documents arrive, and it does not slow down on long lists.
- Errors keep FastAPI's `{"detail": ...}` format, consistent with `/health`.

## 8. Upload flow

1. **Checks in the API.** The body is read in chunks up to `UPLOAD_MAX_BYTES` (default 20 MB) and never fully buffered beyond it. The type is decided by the bytes (`%PDF-`), never by `Content-Type` or the extension; only PDFs are accepted. The file name is only used for the title, after cleaning, and never as a path. Per-user limits: 20 uploads per hour and 200 documents (configurable).
2. **Write.** The bytes go to the `BlobStore` on a volume shared by the API and the pipeline. One transaction, under RLS, creates the artifact and the document with the user's `owner_id`.
3. **Answer** `202`; the client polls `GET /documents/{id}` until `ready` or `failed`.
4. **Pipeline.** A second Prefect deployment, `process-pending`, runs every 30 seconds and calls the existing catch-up code (artifacts without a version for the current extractor).
5. **Concurrency.** The catch-up step takes a PostgreSQL advisory lock (`pg_try_advisory_lock`), so the nightly batch and `process-pending` never process at the same time.

## 9. Configuration

New backend variables, validated at startup: `BLOB_DIR`, `UPLOAD_MAX_BYTES`, `ALLOWED_ORIGINS`, `SESSION_COOKIE_SECURE`, `SESSION_IDLE_SECONDS`, `SESSION_MAX_SECONDS`, and the login and upload limits. Compose mounts the `blobs` volume in the backend too.

## 10. Error handling

| Code | When |
|---|---|
| 401 | no session or expired session |
| 403 | write on a public document |
| 404 | resource not visible (missing or someone else's, indistinguishable) |
| 409 | email already used, document quota reached, sections not ready yet |
| 413 | upload too large |
| 415 | upload is not a PDF |
| 429 | too many login attempts or uploads |
| 503 | Redis or PostgreSQL unavailable |

No response and no log line contains a password, a session token, a password hash or a DSN; a test checks it.

## 11. Testing strategy

- **Unit:** password hashing and verification, sign-up rules, session token and expiry (with a fake Redis), the `Origin` check, upload validation (streamed size cap, magic bytes), title cleaning, cursor encoding.
- **Integration** (real PostgreSQL and Redis):
  - **Isolation test, the success criterion.** Users A and B each own private documents, plus a public corpus. For every document route, A never sees, reads, lists or deletes B's documents, including by guessing their ids. The test enumerates the app's declared routes, so a new unprotected route makes it fail.
  - **RLS safety net on its own.** A raw SQL query with no filter, run as `explorai_app` with A's identity, returns no row of B; with no identity, only public rows.
  - **Full upload path:** `202`, then `process-pending`, then `ready`, with readable sections; a scanned PDF ends `failed`.
  - **Sessions:** logout invalidates the cookie, expiry works, a new login changes the token.
- **Smoke:** sign-up, login, upload and read against the Compose stack.
- Coverage gate stays at 80%.

## 12. Threats and guardrails

| Threat | Guardrail |
|---|---|
| Reading another user's data (IDOR) | repository filter plus RLS; indistinguishable 404; a test that covers every route |
| Session theft | `HttpOnly`/`Secure` cookie, token hashed in Redis, idle and absolute expiry, server-side revocation on logout |
| CSRF | `SameSite=Lax` and an `Origin` allowlist on state-changing requests |
| Brute force, credential stuffing | per-email and per-IP limits, Argon2id, passwords of at least 12 characters |
| Account enumeration | a single login error and constant-time verification; the sign-up `409` is a documented limitation |
| Hostile upload (huge body, fake PDF, crafted file name) | streamed size cap, magic bytes, file name never used as a path, isolated extraction in the pipeline |
| Storage abuse | per-user upload rate and document quota |
| Secret leaks | `hide_input_in_errors`, no secrets in logs or responses, `gitleaks` |
| RLS bypass through SQL injection (`RESET ROLE`) | parameterised queries only; a separate login role is planned in sub-project 10 (documented limitation) |

## 13. Evaluation criteria (definition of done)

1. A user signs up, logs in, uploads a PDF and reads its sections once processed.
2. The isolation test passes on every document route, and the RLS safety-net test passes.
3. Logout and expiry really invalidate the session.
4. The limits (size, type, rate, quota) answer with the documented codes.
5. The OpenAPI contract and the TypeScript client are up to date, and CI is green.
6. The documentation exists: the three ADRs above, a concepts note with interview questions and answers, updated READMEs.

## 14. Dependencies and next step

Depends on sub-projects 0 and 1. After spec approval, write the implementation plan; no code or dependency is added before the plan is approved. Sub-project 3 (RAG) follows and will query documents through the same isolation layers.
