# Explorai backend

FastAPI service for Explorai. Health endpoints:

- `GET /health/live` - liveness: the process is up (always 200).
- `GET /health/ready` - readiness: PostgreSQL and Redis are reachable. Returns 200, or 503 with
  per-dependency status (`{"status": "unavailable", "checks": {"postgres": "down", "redis": "ok"}}`).
  Each check is bounded by `CHECK_TIMEOUT_SECONDS`; error details are never returned or logged.

## API

All business routes are under `/api/v1`; errors use FastAPI's `{"detail": ...}`. The contract is `backend/openapi.json`; the design is in `docs/superpowers/specs/2026-11-01-core-backend-design.md` and `docs/superpowers/specs/2026-11-01-rag-design.md`, the choices in ADRs [0009](../docs/adr/0009-opaque-sessions-over-jwt.md), [0010](../docs/adr/0010-row-level-security-defence-in-depth.md) and, for questions, [0011](../docs/adr/0011-qdrant-and-retrieval-isolation.md) to [0014](../docs/adr/0014-providers-behind-interfaces.md).

### Accounts (`/api/v1/auth`)

| Route | Answers |
| --- | --- |
| `POST /signup` `{email, password}` (password 12 to 128 characters, Argon2id) | 201 `{id, email}`; 409 if the email is taken (case-insensitive); 422 on invalid input (the rejected value is never echoed) |
| `POST /login` | 204 and an `explorai_session` cookie (`HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` unless `SESSION_COOKIE_SECURE=false`); 401 `invalid credentials` for every failure; 429 after `LOGIN_MAX_FAILURES_PER_EMAIL` (5) or `LOGIN_MAX_FAILURES_PER_IP` (20) failures within `LOGIN_WINDOW_SECONDS` (900) |
| `POST /logout` | 204; deletes the session server-side and clears the cookie (also 204 without a cookie) |
| `GET /me` | the current user, or 401 |

Sessions live in Redis as `session:<sha256(token)>` (the raw token is never stored), expire after `SESSION_IDLE_SECONDS` without use and at most `SESSION_MAX_SECONDS` after login. Redis or PostgreSQL down gives 503 (fail closed). Slow counts as down: the Redis client gives up after 2 seconds (`socket_connect_timeout`, `socket_timeout`), a PostgreSQL connection attempt after 5 (`connect_timeout`), and a connection pool exhausted for its `pool_timeout` also answers 503 instead of 500. A client that disconnects mid-upload ends the request quietly (logged at `INFO`, reservation released).

### Documents (`/api/v1/documents`, login required)

| Route | Answers |
| --- | --- |
| `GET /documents?scope=mine\|public\|all&limit=&cursor=` | 200 `{items, next_cursor}`; each item has `id`, `title`, `source_kind`, `owned`, `status` (`processing`, `ready`, `failed`), `created_at`, `indexed` (true once its chunks are in the live search index, see below); `limit` 1 to 100 (default 20); 422 on a bad cursor |
| `GET /documents/{id}` | 200; 404 if missing **or** owned by someone else |
| `GET /documents/{id}/sections?limit=&cursor=` | 200 sections of the latest `ok` version; 404; 409 while there is none yet |
| `POST /documents?filename=paper.pdf` | raw PDF body with `Content-Type: application/pdf`; 202 with the new document (`processing`), 200 with the existing one if you already uploaded the same bytes; 409 quota reached; 413 too large; 415 not a PDF; 429 too many uploads |
| `DELETE /documents/{id}` | 204; 404; 403 for a public document |

`indexed` is computed with one query per page (`ChunkStore.indexed_state`, keyed by the live index name `RagSettings.index_name`, i.e. embedding model and dimension): after a model change documents read `false` until the pipeline has re-indexed them.

### Questions (`POST /api/v1/ask`, login required)

Body `{question, scope: all|mine|public (default all), document_id?}`. `scope` picks the owner filter (`all`: yours and the public corpus; `mine`; `public`); `document_id` narrows the search to one visible document. The answer comes from the `explorai_ai` pipeline (hybrid retrieval in Qdrant under the owner filter, chunk texts re-read from PostgreSQL under RLS, reranking, generation through a forced tool, citation check); see [`ai/README.md`](../ai/README.md) and [`docs/concepts/03-rag.md`](../docs/concepts/03-rag.md).

| Answer | When |
| --- | --- |
| 200 `{status, answer, citations, usage}` | `status` is `answered` (each citation has `source`, `quote`, `chunk_id`, `document_id`, `document_title`, `section`; every quote was checked verbatim against its chunk) or `insufficient_evidence` (`answer` is `null`, no citations): a refusal is a normal answer |
| 404 | `document_id` is unknown **or** someone else's private document (same body) |
| 422 | empty, whitespace-only or longer than 2000 characters question, bad `scope` or `document_id` (the question is never echoed) |
| 429 | more than `RAG_ASK_MAX_PER_HOUR` (30) questions in the user's hourly window |
| 503 | Redis (rate limiter), Qdrant, a model provider or PostgreSQL unavailable |

Each question reserves one unit of the hourly budget before anything else (atomic `INCR` on `ask:user:<id>`); only a served question (answer or refusal) keeps it, every other outcome gives it back. Neither the question nor the answer is logged: codes, counts and durations only. RAG settings (`RAG_PROVIDER` is required: `fake` for tests and the dev stack, `real` needs `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY`; `QDRANT_URL`, `QDRANT_API_KEY`, models and limits, see `ai/`) are validated at startup: a missing or invalid value stops the app and names the variable, never its value.

On state-changing requests an `Origin` header, when present, must be listed in `ALLOWED_ORIGINS` (403 otherwise); requests without one (curl, server-to-server) pass, and browsers always send it on cross-site writes. The examples below set it as the frontend would.

### Try it with curl

With the stack running (see "Run the stack"; the backend listens on port 8000). `-c` saves the session cookie in a jar, `-b` sends it back; the cookie is `Secure` by default, and the dev stack sets `SESSION_COOKIE_SECURE=false` so it works over plain HTTP.

```bash
API=http://127.0.0.1:8000/api/v1
ORIGIN="Origin: http://127.0.0.1:5173"
JSON="Content-Type: application/json"

curl -i -X POST "$API/auth/signup" -H "$ORIGIN" -H "$JSON" \
  -d '{"email":"ada@example.com","password":"a long passphrase 123"}'
curl -i -c jar.txt -X POST "$API/auth/login" -H "$ORIGIN" -H "$JSON" \
  -d '{"email":"ada@example.com","password":"a long passphrase 123"}'
curl -b jar.txt "$API/auth/me"

# Upload a private PDF (202, status "processing"; the pipeline picks it up within ~30 s)
curl -b jar.txt -X POST "$API/documents?filename=paper.pdf" -H "$ORIGIN" \
  -H "Content-Type: application/pdf" --data-binary @paper.pdf

curl -b jar.txt "$API/documents?scope=mine&limit=10"        # follow next_cursor with &cursor=...
curl -b jar.txt "$API/documents/<id>"                       # poll until "status":"ready"
curl -b jar.txt "$API/documents/<id>/sections?limit=5"
curl -b jar.txt "$API/documents/<id>"                       # poll until "indexed":true
curl -b jar.txt -X POST "$API/ask" -H "$ORIGIN" -H "$JSON" \
  -d '{"question":"What does the paper propose?","scope":"mine"}'
# 200 {"status":"answered","answer":"... [1]","citations":[{"source":1,"quote":"...","chunk_id":"...",
#      "document_id":"...","document_title":"...","section":"..."}],"usage":{"input_tokens":...,"output_tokens":...}}
#  or {"status":"insufficient_evidence","answer":null,"citations":[],"usage":{...}}
curl -b jar.txt -X POST "$API/ask" -H "$ORIGIN" -H "$JSON" \
  -d '{"question":"Which datasets are used?","document_id":"<id>"}'   # one document only
curl -b jar.txt -X DELETE "$API/documents/<id>" -H "$ORIGIN"
curl -b jar.txt -c jar.txt -X POST "$API/auth/logout" -H "$ORIGIN"
```

Uploaded files are processed by the `process-pending` deployment of the pipelines service (see `pipelines/README.md`), which every 30 seconds extracts the artifacts that have no version yet; without it running, documents stay `processing`.

### Known limitations

- Anyone can lock a given user out of login for 15 minutes by failing five times for that email (the price of a per-email limit).
- Sign-up answers 409 for a known email, which reveals that the account exists (no email confirmation yet).
- The document quota is soft: a burst of uploads can overshoot it by a few (the hourly limit bounds it). The bandwidth of rejected uploads is not limited by the API; a reverse proxy should do that.
- Behind a proxy, the login IP limit sees the proxy's address unless the deployment forwards the client's (sub-project 10).
- `SET LOCAL ROLE` is not a barrier against SQL injection, and the `explorai_app` role can read `users.password_hash`; a separate login role is planned for sub-project 10 (ADR 0010).
- No password reset, no "log out everywhere", no retry cap for uploads that keep failing.
- A newer failed version over an older good one makes the status `failed` while the sections of the good version are still served.
- Questions (`POST /ask`): private chunk text and the question are sent to Voyage and Anthropic when `RAG_PROVIDER=real` (ADR 0014); the quote of a citation is the model's text matched after NFKC and case folding, and the check proves the quote is in the chunk, not that it supports the claim (ADR 0013); `[n]` markers in the answer are not rewritten when citations are dropped; a CJK quote without spaces never verifies.
- A question's reservation is given back with an optimistic `WATCH`/`MULTI` decrement; if the counter changes concurrently the give-back is dropped (one unit lost until the window ends; a Lua script would make it atomic). The window is fixed, so a burst at the boundary can reach twice the hourly limit.
- `RAG_QUESTION_MAX_CHARS` is not used: the route enforces a fixed 2000 characters.
- Response time reveals the refusal level (a refusal before generation answers much faster than a generated answer). It concerns only the asker's own visible corpus, not other users.
- `indexed` reads `false` for every document during a re-index to a new embedding model, until the pipeline has filled the new collection.
- The real model providers have never been called, and the `/ask` integration tests, the isolation suite `test_rag_isolation.py` and `test_rag_flow.py` have not run yet (Docker was unavailable): CI is their first run.
- Cross-tenant timing oracle on uploads: blobs are content-addressed and shared, and `put()` of bytes already stored by anyone only refreshes the file's mtime instead of writing it. A 20 MB upload of a file someone else already stored therefore answers measurably faster, which lets a user test whether a specific file is stored. Not fixed now (ADR 0010).

## Run

The app reads only the process environment (it does not load `.env` files). From the repository root, start the databases, export the variables from `infra/.env`, then run uvicorn:

```
cp .env.example infra/.env                                                          # once; git-ignored
docker compose --env-file infra/.env -f infra/docker-compose.yml up -d --wait postgres redis
set -a; . infra/.env; set +a                                                        # export DATABASE_URL, REDIS_URL, ...
uv run --package explorai-backend uvicorn explorai_backend.main:app --reload
```

(`set -a; . infra/.env; set +a` is POSIX shell syntax, e.g. Git Bash. On Windows, running uvicorn natively also needs the selector event loop, see Test below.)

## Run the stack

The full stack (backend, frontend, PostgreSQL, Redis, Qdrant and the ingestion and indexing services, with `RAG_PROVIDER=fake` by default) runs in Docker. From the repository root:

```
cp .env.example infra/.env                                                          # once; git-ignored
docker compose --env-file infra/.env -f infra/docker-compose.yml up -d --build --wait
curl http://127.0.0.1:8000/health/ready
docker compose --env-file infra/.env -f infra/docker-compose.yml down -v
```

The backend container runs as a non-root user and reaches the databases by service name (Compose
overrides `DATABASE_URL` and `REDIS_URL`; the `127.0.0.1` values in `infra/.env` are for host-side tests).

## Test

Unit tests need no services:

```
uv run --package explorai-backend pytest backend/tests -m "not integration"
```

Integration tests need real Postgres, Redis and Qdrant (`QDRANT_URL`, `QDRANT_API_KEY`) and `RAG_PROVIDER=fake`; the RAG tests index documents with the real indexing functions of `explorai_pipelines` (a dev-only dependency) and the fake models. Run from the repository root (the `backend/tests`
path matters: it makes pytest pick up `backend/pyproject.toml`):

```
cp .env.example infra/.env                                                          # once; git-ignored
docker compose --env-file infra/.env -f infra/docker-compose.yml up -d --wait       # start the stack
set -a; . infra/.env; set +a                                                        # export DATABASE_URL, REDIS_URL, ...
uv run --package explorai-backend pytest backend/tests -m integration
docker compose --env-file infra/.env -f infra/docker-compose.yml down -v            # stop and remove the stack
```

(`set -a; . infra/.env; set +a` is POSIX shell syntax, e.g. Git Bash.) Integration tests fail, not
skip, when `DATABASE_URL`/`REDIS_URL` are missing.

On Windows, the psycopg async driver needs the selector event loop instead of the default
ProactorEventLoop. The tests handle this in `backend/tests/conftest.py`; running uvicorn natively on
Windows would need the same, while Linux containers do not.

### Isolation suite

`backend/tests/integration/test_isolation.py` is the success criterion of the sub-project: two users with private documents plus a public one, every document route swept for cross-user access (404), every business route checked for the login requirement, and a raw unfiltered query run as `explorai_app` to prove that Row-Level Security holds on its own. It reads the routes from the OpenAPI schema, so a new route fails it until the sweep is extended. Run it alone (same preparation as above):

```
uv run --package explorai-backend pytest backend/tests/integration/test_isolation.py --no-cov
```

The RAG counterpart is `backend/tests/integration/test_rag_isolation.py`, the success criterion of sub-project 3: users A and B with private indexed documents plus a public one; for questions built to match B's text, none of B's chunks, titles or quotes appear in A's candidates, in any prompt seen by a recording generator, or in A's answer. One variant replaces the Qdrant filter with one that matches everything and removes the store's owner condition, so only Row-Level Security remains. `test_rag_flow.py` covers upload to cited answer over HTTP, refusal without generation, deletion and prune, the empty corpus and the disabled stages. Both need Qdrant as well, and **have not been run yet** (Docker was unavailable when they were written); CI is their first run.

```
uv run --package explorai-backend pytest backend/tests/integration/test_rag_isolation.py --no-cov
```

Integration tests use a throwaway migrated database (`explorai_db.testing.temporary_database`, so `DATABASE_URL` must be a PostgreSQL superuser) and Redis database 15, flushed per test. `backend/tests/unit/test_architecture.py` (no services) fails if backend code opens a database session other than through `scoped_session`, including by calling anything named like a session factory (`self._factory()`). Repositories receive `explorai_db.rls.ScopedSessions` from `deps.get_scoped_sessions`, never the factory itself: its only method, `scoped(user_id)`, opens an identity-scoped transaction.

Coverage must stay at or above 80%.

## OpenAPI contract

`backend/openapi.json` is the versioned API contract (deterministic: sorted keys, 2-space indent, LF).
Regenerate it after any route or schema change, from the repository root (no services or env vars needed):

```
uv run --package explorai-backend python backend/scripts/export_openapi.py
```

A unit test fails when the committed file drifts from the app.

## Environment variables

Read from the process environment only (no `.env` loading); the app fails fast if one is invalid. The RAG settings are read at startup too: a missing `RAG_PROVIDER` or an invalid `RAG_*` value stops the app with `invalid RAG configuration: <VARIABLE>: <problem>`, never the value.

| Variable | Required | Description |
| --- | --- | --- |
| `APP_ENV` | yes | `development`, `test` or `production` |
| `DATABASE_URL` | yes | PostgreSQL DSN, e.g. `postgresql://user:pass@127.0.0.1:5432/explorai` |
| `REDIS_URL` | yes | Redis DSN, e.g. `redis://127.0.0.1:6379/0` |
| `CHECK_TIMEOUT_SECONDS` | no | Per-dependency readiness timeout, default `2.0` |
| `BLOB_DIR` | yes | Directory of the blob store, shared with the pipelines (the Compose `blobs` volume, `/data/blobs` in containers) |
| `UPLOAD_MAX_BYTES` | no | Largest accepted upload, default `20000000` |
| `ALLOWED_ORIGINS` | no | Comma-separated origins accepted on state-changing requests, default `http://127.0.0.1:5173` |
| `SESSION_COOKIE_SECURE` | no | `true` (default) sends the cookie over HTTPS only; the dev Compose stack sets `false`. Keep `true` in production |
| `SESSION_IDLE_SECONDS` | no | Idle session expiry, default `86400` |
| `SESSION_MAX_SECONDS` | no | Absolute session lifetime, default `604800` |
| `LOGIN_MAX_FAILURES_PER_EMAIL` | no | Failures per email before 429, default `5` |
| `LOGIN_MAX_FAILURES_PER_IP` | no | Failures per IP address before 429, default `20` |
| `LOGIN_WINDOW_SECONDS` | no | Window of the two login limits, default `900` |
| `UPLOAD_MAX_PER_HOUR` | no | Uploads that create a document, per user and hour, default `20` |
| `MAX_DOCUMENTS_PER_USER` | no | Document quota per user, default `200` |
| `RAG_PROVIDER` | yes | `fake` (deterministic fakes, no key: tests, CI, the dev stack) or `real` |
| `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY` | with `real` | provider keys (unprefixed names); never logged or echoed |
| `QDRANT_URL` | no | Qdrant base URL, default `http://127.0.0.1:6333` (Compose sets `http://qdrant:6333`) |
| `QDRANT_API_KEY` | in Compose | Qdrant API key (unprefixed name) |
| `RAG_ASK_MAX_PER_HOUR` | no | Questions per user and hour, default `30` |
| `RAG_*` (models, candidate counts, threshold, timeouts, switches) | no | the library's settings, all with defaults: full table in [`ai/README.md`](../ai/README.md#settings) |

If the PostgreSQL password contains `@`, `/` or `:`, URL-encode it inside `DATABASE_URL` (for example `@` becomes `%40`). `POSTGRES_PASSWORD` keeps the raw value.

Use `127.0.0.1` rather than `localhost`: on Windows `localhost` tries `::1` first and stalls each probe (about 5 s for PostgreSQL through libpq, about 2 s for Redis).
