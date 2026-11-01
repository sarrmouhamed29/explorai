# Explorai backend

FastAPI service for Explorai. Health endpoints:

- `GET /health/live` - liveness: the process is up (always 200).
- `GET /health/ready` - readiness: PostgreSQL and Redis are reachable. Returns 200, or 503 with
  per-dependency status (`{"status": "unavailable", "checks": {"postgres": "down", "redis": "ok"}}`).
  Each check is bounded by `CHECK_TIMEOUT_SECONDS`; error details are never returned or logged.

## API

All business routes are under `/api/v1`; errors use FastAPI's `{"detail": ...}`. The contract is `backend/openapi.json`; the design is in `docs/superpowers/specs/2026-11-01-core-backend-design.md` and the choices in ADRs [0009](../docs/adr/0009-opaque-sessions-over-jwt.md) and [0010](../docs/adr/0010-row-level-security-defence-in-depth.md).

### Accounts (`/api/v1/auth`)

| Route | Answers |
| --- | --- |
| `POST /signup` `{email, password}` (password 12 to 128 characters, Argon2id) | 201 `{id, email}`; 409 if the email is taken (case-insensitive); 422 on invalid input (the rejected value is never echoed) |
| `POST /login` | 204 and an `explorai_session` cookie (`HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` unless `SESSION_COOKIE_SECURE=false`); 401 `invalid credentials` for every failure; 429 after `LOGIN_MAX_FAILURES_PER_EMAIL` (5) or `LOGIN_MAX_FAILURES_PER_IP` (20) failures within `LOGIN_WINDOW_SECONDS` (900) |
| `POST /logout` | 204; deletes the session server-side and clears the cookie (also 204 without a cookie) |
| `GET /me` | the current user, or 401 |

Sessions live in Redis as `session:<sha256(token)>` (the raw token is never stored), expire after `SESSION_IDLE_SECONDS` without use and at most `SESSION_MAX_SECONDS` after login. Redis or PostgreSQL down gives 503 (fail closed).

### Documents (`/api/v1/documents`, login required)

| Route | Answers |
| --- | --- |
| `GET /documents?scope=mine\|public\|all&limit=&cursor=` | 200 `{items, next_cursor}`; each item has `id`, `title`, `source_kind`, `owned`, `status` (`processing`, `ready`, `failed`), `created_at`; `limit` 1 to 100 (default 20); 422 on a bad cursor |
| `GET /documents/{id}` | 200; 404 if missing **or** owned by someone else |
| `GET /documents/{id}/sections?limit=&cursor=` | 200 sections of the latest `ok` version; 404; 409 while there is none yet |
| `POST /documents?filename=paper.pdf` | raw PDF body with `Content-Type: application/pdf`; 202 with the new document (`processing`), 200 with the existing one if you already uploaded the same bytes; 409 quota reached; 413 too large; 415 not a PDF; 429 too many uploads |
| `DELETE /documents/{id}` | 204; 404; 403 for a public document |

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
curl -b jar.txt -X DELETE "$API/documents/<id>" -H "$ORIGIN"
curl -b jar.txt -c jar.txt -X POST "$API/auth/logout" -H "$ORIGIN"
```

Uploaded files are processed by the `process-pending` deployment of the pipelines service (see `pipelines/README.md`); without it running, documents stay `processing`.

### Known limitations

- Anyone can lock a given user out of login for 15 minutes by failing five times for that email (the price of a per-email limit).
- Sign-up answers 409 for a known email, which reveals that the account exists (no email confirmation yet).
- The document quota is soft: a burst of uploads can overshoot it by a few (the hourly limit bounds it). The bandwidth of rejected uploads is not limited by the API; a reverse proxy should do that.
- Behind a proxy, the login IP limit sees the proxy's address unless the deployment forwards the client's (sub-project 10).
- `SET LOCAL ROLE` is not a barrier against SQL injection, and the `explorai_app` role can read `users.password_hash`; a separate login role is planned for sub-project 10 (ADR 0010).
- No password reset, no "log out everywhere", no retry cap for uploads that keep failing.
- A newer failed version over an older good one makes the status `failed` while the sections of the good version are still served.

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

The full stack (backend, frontend, PostgreSQL, Redis and the ingestion services) runs in Docker. From the repository root:

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

Integration tests need real Postgres and Redis. Run from the repository root (the `backend/tests`
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

Integration tests use a throwaway migrated database (`explorai_db.testing.temporary_database`, so `DATABASE_URL` must be a PostgreSQL superuser) and Redis database 15, flushed per test. `backend/tests/unit/test_architecture.py` (no services) fails if backend code opens a database session other than through `scoped_session`.

Coverage must stay at or above 80%.

## OpenAPI contract

`backend/openapi.json` is the versioned API contract (deterministic: sorted keys, 2-space indent, LF).
Regenerate it after any route or schema change, from the repository root (no services or env vars needed):

```
uv run --package explorai-backend python backend/scripts/export_openapi.py
```

A unit test fails when the committed file drifts from the app.

## Environment variables

Read from the process environment only (no `.env` loading); the app fails fast if one is invalid.

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

If the PostgreSQL password contains `@`, `/` or `:`, URL-encode it inside `DATABASE_URL` (for example `@` becomes `%40`). `POSTGRES_PASSWORD` keeps the raw value.

Use `127.0.0.1` rather than `localhost`: on Windows `localhost` tries `::1` first and stalls each probe (about 5 s for PostgreSQL through libpq, about 2 s for Redis).
