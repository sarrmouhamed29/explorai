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

```
uv run --package explorai-backend pytest backend/tests/unit            # unit tests, no services needed
uv run --package explorai-backend pytest -m integration                # needs real Postgres and Redis
```

Coverage must stay at or above 80%.

## Environment variables

Read from the process environment only (no `.env` loading); the app fails fast if one is invalid.

| Variable | Required | Description |
| --- | --- | --- |
| `APP_ENV` | yes | `development`, `test` or `production` |
| `DATABASE_URL` | yes | PostgreSQL DSN, e.g. `postgresql://user:pass@localhost:5432/explorai` |
| `REDIS_URL` | yes | Redis DSN, e.g. `redis://localhost:6379/0` |
| `CHECK_TIMEOUT_SECONDS` | no | Per-dependency readiness timeout, default `2.0` |
