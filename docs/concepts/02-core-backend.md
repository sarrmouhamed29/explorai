# Core backend: concepts and interview questions

Concept notes for sub-project 2. Each concept is explained plainly, then tied to the files where it lives. The questions at the end are practice for an English interview; the answers are short on purpose. The design is in `docs/superpowers/specs/2026-11-01-core-backend-design.md`; the choices are in ADR [0008](../adr/0008-shared-db-package.md) (amended), [0009](../adr/0009-opaque-sessions-over-jwt.md) and [0010](../adr/0010-row-level-security-defence-in-depth.md).

## 1. A persistence layer shared by producers and consumers

**Idea.** The API writes uploads and the pipeline processes them, so both touch the same rows and the same files. If one imported the other, a refactor of one would break the other. A third package owns what they share.

**In this repo.** `db/` (`explorai_db`) holds the models, migrations, the blob store (`explorai_db.blobs`, moved out of `pipelines/`) and the identity-scoped session (`explorai_db.rls`). The backend is layered on top: routes (`api/`) parse HTTP and map exceptions to status codes, services (`services/`) hold the use cases and raise domain exceptions (`DocumentNotFound`, `QuotaReached`), repositories (`repositories/`) hold the queries. The upload is a durable queue in the database: the API stores the bytes and the rows and answers `202`; `process-pending` later adds the version. The API never calls Prefect, so an upload works even if Prefect is down.

## 2. Row-Level Security, roles and `SET LOCAL`

**Idea.** An IDOR (insecure direct object reference) is a route that takes an id and forgets to check ownership. Row-Level Security (RLS) moves the check into the database: a policy is a predicate that PostgreSQL adds to every query on a table, whatever the application wrote. The policy needs to know who is asking, so each transaction sets a variable, and a role without `BYPASSRLS` makes the policy apply.

**In this repo.** `scoped_session(factory, user_id)` opens one transaction, runs `SET LOCAL ROLE explorai_app` and `set_config('app.user_id', ..., true)`. `SET LOCAL` and the `true` argument mean "until the end of this transaction", so a pooled connection never carries a previous user's identity into the next request. The SQL function `app_user()` reads the setting; policies compare `owner_id` to it. A version or section is visible exactly when its document is, because the policy on `document_versions` runs a subquery on `documents`, which is itself filtered by the documents policy (the subquery inherits the policy). RLS is `ENABLE`d without `FORCE`: the table owner (the pipeline) bypasses the policies, the API never runs as the owner, and an AST architecture test (`backend/tests/unit/test_architecture.py`) fails if backend code opens a session any other way. The raw factory is not even reachable from request code: `deps.get_scoped_sessions` hands repositories a `ScopedSessions` wrapper whose only method is `scoped(user_id)`, so the tempting shortcut `with self._factory() as session:` (which would run as the owner) cannot be written by accident, and the architecture test also flags any call of something named `*factory*`. Limits, honestly stated: `SET LOCAL ROLE` is not a hard boundary against SQL injection (`RESET ROLE` would undo it), a `Session` reused after its block would run as the owner, and `explorai_app` can read `users.password_hash`. See ADR 0010.

## 3. Dependency injection of infrastructure, CSRF and the `Origin` header

**Idea.** Dependency injection means a function declares what it needs (a session factory, a Redis client, the settings) and the framework supplies it, so tests can substitute fakes. CSRF (cross-site request forgery) is a hostile page making your browser send a request to another site, with your cookies attached.

**In this repo.** `deps.py` builds the engine, Redis client and blob store once (`lru_cache`) and exposes them to `Depends(...)`; it is the only module allowed to build them. `config.py` validates settings at startup and hides input values in errors. Against CSRF there are two layers: the cookie is `SameSite=Lax` (the browser does not attach it to cross-site `POST`s), and `OriginCheckMiddleware` refuses `POST`/`PUT`/`PATCH`/`DELETE` whose `Origin` header is not in `ALLOWED_ORIGINS` (`403`). Scripts cannot forge `Origin`; a request without one comes from a non-browser client, which is not a CSRF vector, so it passes.

## 4. Passwords, sessions, constant-time verification, rate limiting

**Idea.** A password is never stored; a hash is. A fast hash (SHA-256) lets an attacker try billions of guesses per second on a GPU, so a password hash must be slow and memory-hungry (Argon2id: each guess costs 64 MiB). A salt is a random value unique to each password, stored inside the hash string; it makes two equal passwords hash differently and kills precomputed tables. A session is the server remembering who a random token belongs to.

**In this repo.** `auth/passwords.py` (Argon2id, `verify_dummy` for unknown emails so response time does not reveal which accounts exist), `auth/sessions.py` (token `secrets.token_urlsafe(32)`, Redis key `session:<sha256(token)>`, idle TTL renewed on use, absolute expiry from the stored creation time, `revoke` on logout), `auth/limits.py` (fixed-window counters: `INCR` is atomic, so each request reserves its attempt before any hashing and parallel bursts cannot all pass a read-then-count check; a success clears the email's failures and is not counted against the IP). A new token is minted at every login and the old session is revoked, which prevents session fixation (an attacker planting a known token before the victim logs in). Redis down gives `503`, never an open door.

## 5. Keyset pagination, computed status, 404 versus 403

**Idea.** Offset pagination (`OFFSET 1000`) makes the database walk past 1000 rows, and shifts when rows are inserted meanwhile. Keyset pagination remembers the sort key of the last row and asks for "rows after this key", which seeks straight through an index and stays stable.

**In this repo.** `api/pagination.py` encodes `(created_at, id)` as an opaque base64 cursor (the id breaks ties); the repository orders by `created_at DESC, id DESC` and filters with a row comparison `(created_at, id) < (:t, :i)`; the service fetches `limit + 1` rows to know whether a next page exists without a `COUNT`. Sections use the `ordinal`. A forged cursor is a `422`, bounded to the 32-bit column so it can never reach the database as a `500`. A document's status is **computed**, not stored: no version means `processing`, latest version `ok` means `ready`, `unextractable` means `failed`. The latest version decides, so a newer failed version over an older good one shows `failed` while sections still serve the latest `ok` one (pinned by a test). Someone else's document answers `404`, exactly like a missing one, so the API does not confirm that an id exists; only a public document on a write answers `403`, because its existence is not secret.

## 6. Streaming uploads, magic bytes, idempotent creation, quotas

**Idea.** A server must not trust the client's size, type or name. Reading a body as a stream lets it stop at the cap instead of buffering gigabytes. The type is decided by the bytes: every PDF starts with `%PDF-`. Idempotent means that doing it twice equals doing it once.

**In this repo.** The upload is a raw body, `POST /api/v1/documents?filename=...` with `Content-Type: application/pdf`, not multipart: Starlette receives a multipart body completely before the handler runs, so a streamed cap is only possible on a raw body. `uploads.read_capped` checks the size after every chunk and a declared `Content-Length` over the cap is refused without reading (`413`); `require_pdf` checks the magic bytes (`415`), ignoring `Content-Type` and the extension; `clean_title` uses the file name for display only (basename, no control or direction-override characters, 200 characters), never as a path. The document's `external_id` is the SHA-256 of the bytes, so the same owner uploading the same file again gets the existing document (`200` instead of `202`), and `INSERT ... ON CONFLICT DO NOTHING` keeps that true for two concurrent uploads. Quotas: 20 uploads per hour per user (only uploads that create a document consume the budget), 200 documents per user (a soft limit: a burst can overshoot by a few). The file name is never trusted and the extraction itself runs isolated in the pipeline.

## 7. Testing an invariant across every route

**Idea.** "User A never sees user B's data" is a property of the whole API, not of one route. Testing route by route proves each route as written and says nothing about the next one added. A sweep derives the list of routes from the app itself and applies the same assertions to each.

**In this repo.** `backend/tests/integration/test_isolation.py` builds two users with private documents plus a public one, enumerates the routes under `/api/v1` from the OpenAPI schema, and asserts that A gets `404` on B's id for the read, sections and delete routes, that B's document never appears in A's list in any scope (`mine`, `public`, `all`) while the pages terminate, that every business route answers `401` without a session (except signup, login and logout, which need none; logout without a cookie answers `204` by design), and that guessing ids reveals nothing. `test_new_routes_are_covered` compares the discovered routes with an expected set, so a new route fails the suite until someone extends the sweep. A separate test removes the application filter altogether: a raw `SELECT` as `explorai_app` with A's identity returns A's and the public rows but no row of B. The case without identity (only public rows) is tested in `db/tests/integration/test_rls.py`. The same idea is the architecture test: a structural rule checked for the whole code base.

## 8. Advisory locks and garbage collection with a grace period

**Idea.** A PostgreSQL advisory lock is a named mutex held by a session, not tied to any table. `pg_try_advisory_lock` returns at once with true or false, so a second worker can skip instead of waiting. A garbage collector deletes what nothing references, but a naive one races with writers: a blob can look unreferenced just before the transaction that references it commits.

**In this repo.** `Repository.advisory_lock` takes `pg_try_advisory_lock(CATCH_UP_LOCK)` on a dedicated connection for the whole extraction (a transaction-level lock would end with the first commit, and PostgreSQL releases a session lock itself if the process dies). `reextract`, used by `process-pending` every 30 seconds and by the end of every nightly batch, returns `skipped` when it does not get the lock. `process-pending` passes `only_unversioned=True` and reads only artifacts with no version at all: without it, each pass would read every blob in the store, because a PDF never has an `html-*` version and the per-extractor query returns the whole corpus. Extractor version upgrades stay with the nightly catch-up and the manual `reextract` command. `collect_blobs` deletes only blobs that no artifact references **and** that are older than `BLOB_GRACE_SECONDS` (one hour): the grace period protects an upload whose rows are not committed yet. Two remaining races are closed in the store: `put` refreshes the age of an existing blob, and `delete_if_older_than` renames the file aside atomically, re-checks its age, and restores it if it was refreshed. The API itself never deletes blobs: under RLS it cannot see whether another owner's artifact shares the same content-addressed blob.

## 9. Contract-first integration and an end-to-end test of the user journey

**Idea.** The OpenAPI document is the agreement between backend and frontend. An end-to-end (smoke) test exercises the real deployed stack, where wiring mistakes (a missing volume, a proxy, an environment variable) live; unit tests cannot see those.

**In this repo.** Every route change regenerates `backend/openapi.json` and `frontend/src/api/schema.d.ts`; a unit test and the CI `contract` job fail on drift. `infra/smoke.sh` signs up two users through the frontend origin (so the Vite proxy, `Origin` check and cookie are on the path), logs in, uploads a PDF, waits for the 30-second pipeline deployment, reads the sections, and checks that the second user gets `404`. At the time of writing the script and the integration suites have been written but not yet run against this code on the maintainer's machine (Docker was unavailable); they must pass locally or on CI before merging.

## 10. Documentation is part of done

**Idea.** A decision that is not written down gets re-argued, and a limitation nobody documented becomes a surprise in production.

**In this repo.** This note, ADRs 0009 and 0010, the amendment of 0008, the READMEs of `backend/`, `db/` and `pipelines/`, and the "Refinements during planning" section of the spec, which records the six places where the plan improved on the spec and why.

## Interview questions and answers

**1. What is an IDOR and how does this project prevent it?**
An insecure direct object reference: a route accepts an id and does not check that the caller may access it. Here every repository query filters by owner, someone else's document answers `404`, and PostgreSQL Row-Level Security enforces the same rule even if a filter is forgotten. An isolation test sweeps every route.

**2. Why both application filters and RLS? Is that not redundant?**
It is deliberate redundancy. Filters alone fail silently when one is forgotten; RLS alone fails silently if a connection runs as the table owner, and hides intent in the queries. With both, one catches the other's failure, and a test that removes the filter proves the policies alone are sufficient.

**3. Why `SET LOCAL ROLE` per transaction rather than `SET ROLE` per connection?**
Connections are pooled and outlive requests. A role or user id set for the connection would stay on it for the next request unless something resets it. `SET LOCAL` and `set_config(..., true)` end automatically with the transaction, so there is nothing to forget.

**4. Why is RLS enabled without `FORCE`? What are the risks?**
The table owner bypasses RLS unless `FORCE` is on. The pipeline runs as the owner and must ingest for every user, so it needs the bypass. The risk is that the API silently runs as the owner if someone opens a session directly, so an AST-based architecture test forbids that outside `deps.py`. I also state the limit: `SET LOCAL ROLE` guards against bugs, not SQL injection, because injected SQL could `RESET ROLE`.

**5. Why opaque sessions instead of JWT?**
Immediate revocation (logout deletes one Redis key), no token contents to leak or misuse, and less to get wrong than access plus refresh tokens. JWT's advantage, stateless verification, matters for many services; I have one API and already run Redis. The price is a Redis lookup per request and a fail-closed `503` when Redis is down.

**6. What do `HttpOnly`, `Secure` and `SameSite` each protect against?**
`HttpOnly` hides the cookie from JavaScript, so an XSS cannot read the session token. `Secure` keeps it off plain HTTP, where it could be sniffed. `SameSite=Lax` stops the browser from attaching it to cross-site subrequests and `POST`s, which blocks most CSRF. They do not replace each other.

**7. How do you defend against CSRF here?**
`SameSite=Lax` plus an `Origin` check on `POST`, `PUT`, `PATCH` and `DELETE`: browsers always send `Origin` on cross-site writes and scripts cannot forge it. A request without `Origin` is a non-browser client. A token-based scheme would add state for little gain given these two layers, and the API is JSON or raw-body only, not form posts.

**8. Why Argon2id, and what is a salt for?**
Argon2id is memory-hard, so GPUs and ASICs gain little: each guess costs 64 MiB. A salt is a random value per password, stored in the hash string, so identical passwords hash differently and precomputed rainbow tables are useless. It does not slow a targeted attack on one hash; the slowness of Argon2 does.

**9. How is login constant-time, and what does that protect?**
When the email is unknown, the server still verifies a password against a dummy hash with the same parameters, and every failure returns the same `401 invalid credentials`. Otherwise response time or message would reveal which emails have accounts. The comparison inside the library is constant-time too. The sign-up `409` still reveals existing emails; closing that needs email confirmation, and I documented it.

**10. How does your login rate limiting work and what are its weaknesses?**
A fixed-window counter in Redis per email (5 failures in 15 minutes) and per IP (20). The attempt is reserved with an atomic `INCR` before hashing, so parallel requests cannot all slip under the limit. Weaknesses: a fixed window allows up to twice the limit across a boundary; the per-email limit lets someone lock a victim out for 15 minutes; behind a proxy the IP must be the real client's. Alternatives are sliding windows or token buckets, at the cost of more state.

**11. Why keyset pagination instead of offsets?**
Offset makes the database skip rows, so deep pages get slower, and pages shift when rows are inserted. A keyset cursor remembers the last `(created_at, id)` and seeks to the next rows through the index. The cost is that you cannot jump to page N, which a feed or a list does not need.

**12. Why `404` instead of `403` for someone else's document?**
A `403` says "this exists but is not yours", which lets an attacker confirm ids. `404` makes missing and forbidden indistinguishable. A public document on a write is `403` because its existence is public anyway and the client deserves the real reason.

**13. How do you handle uploads safely?**
The body is a raw stream, capped while reading (20 MB), so a hostile client cannot make the server buffer more, even with a lying or missing `Content-Length`. The type is decided by magic bytes (`%PDF-`), not by `Content-Type` or extension. The file name only becomes a cleaned title. Per-user rate and quota limit abuse, and the PDF is parsed later in an isolated process. Multipart was rejected because the framework buffers it entirely before my code runs.

**14. What makes an upload idempotent, and why does it matter?**
The document identity is (upload source, SHA-256 of the bytes, owner). Uploading the same file again returns the existing document with `200`, and `ON CONFLICT DO NOTHING` makes concurrent uploads converge. It matters because clients retry on timeouts, and because it avoids storing duplicates.

**15. Why an advisory lock, and why is it session-level and non-blocking?**
The nightly batch and the 30-second `process-pending` must not extract the same files at once. `pg_try_advisory_lock` needs no table, returns immediately so the loser skips instead of queueing, and is released by PostgreSQL if the process dies. It is session-level on a dedicated connection because a transaction-level lock would end at the first commit.

**16. Why a grace period in blob garbage collection?**
An upload writes the blob first and commits the rows after. In between, the blob is unreferenced and would look like garbage. Collecting only blobs older than an hour that nothing references avoids deleting it. The remaining races (a re-upload of an old orphan) are closed by refreshing the age in `put` and by `delete_if_older_than`, which renames the file aside and re-checks before deleting.

**17. Why test an invariant across all routes instead of testing each route?**
Per-route tests prove the routes that exist today. The property I care about, no cross-user access, must also hold for the route someone adds next year. The sweep derives routes from the OpenAPI schema and fails when a route is not in the expected set, so safety does not depend on remembering to write a test.

**18. What would you improve before production?**
A separate database login for the API (so `RESET ROLE` and reading `password_hash` stop being possible), email confirmation and password reset, a reverse proxy that limits request bandwidth and passes the real client IP, Redis persistence, and a retry cap for uploads that keep failing. Most of this is sub-project 10; none of it is hidden, each is listed as a known limitation.
