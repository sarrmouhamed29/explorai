# Explorai frontend

Vite + React + TypeScript dashboard. It talks to the backend through a typed client generated from the committed OpenAPI contract (`backend/openapi.json`), so API types cannot drift by hand. For now it shows the backend readiness (`GET /health/ready`).

## Install

```bash
pnpm --dir frontend install
```

## Run

```bash
pnpm --dir frontend dev
```

Serves http://127.0.0.1:5173. In development, Vite proxies `/health` to `BACKEND_URL` (default `http://127.0.0.1:8000`), which avoids CORS. Use `127.0.0.1`, not `localhost` (on Windows `localhost` resolves to `::1` first and stalls). To bind all interfaces (e.g. in Docker): `pnpm --dir frontend dev --host 0.0.0.0`.

## Test and check

```bash
pnpm --dir frontend test
pnpm --dir frontend typecheck
pnpm --dir frontend lint
pnpm --dir frontend build
```

## Regenerate the API client

After the backend contract changes (`backend/openapi.json`):

```bash
pnpm --dir frontend gen:api
```

This rewrites `src/api/schema.d.ts`, which is committed.
