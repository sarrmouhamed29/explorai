# Explorai frontend

Vite + React + TypeScript dashboard. It talks to the backend through a typed client generated from the committed OpenAPI contract (`backend/openapi.json`), so API types cannot drift by hand. For now it shows the backend readiness (`GET /health/ready`), polled every 5 seconds (`POLL_INTERVAL_MS` in `src/hooks/useBackendHealth.ts`); the last state stays on screen between refreshes and polling stops when the page unmounts.

## Install

```bash
pnpm --dir frontend install
```

## Run

```bash
pnpm --dir frontend dev
```

Locally, `server.host` in `vite.config.ts` defaults to `127.0.0.1` (loopback only) and the server listens on port 5173; the Docker image overrides the host with `--host 0.0.0.0`. Use `127.0.0.1`, not `localhost` (on Windows `localhost` resolves to `::1` first and stalls). In development, Vite proxies `/health` to `BACKEND_URL` (default `http://127.0.0.1:8000`), which avoids CORS.

## Run the full stack (Docker Compose)

From the repo root, this starts Postgres, Redis, the backend and the frontend (dev image, non-root):

```bash
docker compose --env-file infra/.env -f infra/docker-compose.yml up -d --build --wait
```

In Docker the dev server binds `0.0.0.0` and is published on `127.0.0.1:5173` only; `BACKEND_URL` is `http://backend:8000`. Stop with `docker compose --env-file infra/.env -f infra/docker-compose.yml down -v`.

End-to-end smoke test (brings the stack up, checks backend, frontend and the proxied `/health/ready`, then tears everything down):

```bash
bash infra/smoke.sh
```

## Test and check

```bash
pnpm --dir frontend test
pnpm --dir frontend typecheck
pnpm --dir frontend lint
pnpm --dir frontend build
```

## Design notes

- **TypeScript is pinned to `^5`** in `package.json`: TypeScript 7 breaks `openapi-typescript`, which generates the API types. Do not widen the range without checking that `pnpm gen:api` still works.
- **`baseUrl: window.location.origin`** in `src/api/client.ts` assumes the frontend and the API are served from the same origin: the Vite dev proxy now, a reverse proxy later. Serving them from different origins would need an explicit API URL and CORS on the backend.

## Regenerate the API client

After the backend contract changes (`backend/openapi.json`):

```bash
pnpm --dir frontend gen:api
```

This rewrites `src/api/schema.d.ts`, which is committed.
