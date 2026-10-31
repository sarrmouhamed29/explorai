# Explorai backend

FastAPI service for Explorai. Currently exposes health endpoints:

- `GET /health/live` - liveness: the process is up (always 200).
- `GET /health/ready` - readiness: PostgreSQL and Redis are reachable. Returns 200, or 503 with
  per-dependency status (`{"status": "unavailable", "checks": {"postgres": "down", "redis": "ok"}}`).
  Each check is bounded by `CHECK_TIMEOUT_SECONDS`; error details are never returned or logged.

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

The full stack (backend, PostgreSQL, Redis) runs in Docker. From the repository root:

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

If the PostgreSQL password contains `@`, `/` or `:`, URL-encode it inside `DATABASE_URL` (for example `@` becomes `%40`). `POSTGRES_PASSWORD` keeps the raw value.

Use `127.0.0.1` rather than `localhost`: on Windows `localhost` tries `::1` first and stalls each probe (about 5 s for PostgreSQL through libpq, about 2 s for Redis).
