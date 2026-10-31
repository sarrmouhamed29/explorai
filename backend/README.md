# Explorai backend

FastAPI service for Explorai. Currently exposes health endpoints:

- `GET /health/live` - liveness: the process is up (always 200).
- `GET /health/ready` - readiness: PostgreSQL and Redis are reachable. Returns 200, or 503 with
  per-dependency status (`{"status": "unavailable", "checks": {"postgres": "down", "redis": "ok"}}`).
  Each check is bounded by `CHECK_TIMEOUT_SECONDS`; error details are never returned or logged.

## Run

From the repository root, with the environment variables below set:

```
uv run --package explorai-backend uvicorn explorai_backend.main:app --reload
```

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

## Environment variables

Read from the process environment only (no `.env` loading); the app fails fast if one is invalid.

| Variable | Required | Description |
| --- | --- | --- |
| `APP_ENV` | yes | `development`, `test` or `production` |
| `DATABASE_URL` | yes | PostgreSQL DSN, e.g. `postgresql://user:pass@127.0.0.1:5432/explorai` |
| `REDIS_URL` | yes | Redis DSN, e.g. `redis://127.0.0.1:6379/0` |
| `CHECK_TIMEOUT_SECONDS` | no | Per-dependency readiness timeout, default `2.0` |

Use `127.0.0.1` rather than `localhost`: on Windows `localhost` tries `::1` first and stalls each probe by about 2 s.
