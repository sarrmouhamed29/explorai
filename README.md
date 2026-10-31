# Explorai

Explorai is an AI-powered research and decision intelligence platform. The long-term goal is to let a user upload documents, ask questions answered with verifiable citations (RAG), run research agents, apply analytics and ML, and receive scheduled reports. It is also a learning project: every technical choice is documented with the alternatives that were rejected (see `docs/adr/` and `plan.md`).

## Status

Sub-project 0, **Foundations**, is implemented as a walking skeleton: the smallest version of the system that crosses every layer, with no business feature yet.

- Backend: FastAPI service with `GET /health/live` and `GET /health/ready` (checks PostgreSQL and Redis).
- Frontend: one React page that shows the backend readiness, through a TypeScript client generated from the backend's OpenAPI contract.
- Docker Compose stack: PostgreSQL (pgvector image), Redis, backend, frontend (and, since ingestion, `migrate`, `prefect` and `pipelines`).
- Tests, pre-commit hooks and GitHub Actions CI.

Sub-project 1, **Ingestion**, is implemented on top of it: a pipeline that collects arXiv papers and web pages, extracts and cleans their text, marks duplicates and stores versioned text in PostgreSQL.

- `db/`: the shared schema (SQLAlchemy models, Alembic migrations).
- `pipelines/`: sources, safe download, PDF and HTML extraction, cleaning, deduplication, Prefect flows and a nightly schedule. Replaying a batch changes nothing.
- Compose gains a one-shot `migrate` service, a Prefect server (<http://127.0.0.1:4200>) and the `pipelines` service.
- Measured baseline for PDF extraction: [`docs/evaluation/extraction-baseline.md`](docs/evaluation/extraction-baseline.md).

There is no user-facing feature yet (no search, no answers). Sub-project 2 (Core backend) is next. The full roadmap is in [`plan.md`](plan.md).

## Prerequisites

- Docker Desktop (Compose v2)
- [uv](https://docs.astral.sh/uv/) (the CI uses 0.13.6)
- Python 3.12 or newer (uv can provision it)
- Node 22 and pnpm 12.16.1
- gitleaks v8.30.1 on your `PATH` (only needed for the commit hooks, see [CONTRIBUTING.md](CONTRIBUTING.md#local-hooks))
- bash (Git Bash on Windows): the smoke test and some hooks are shell scripts

## Quick start

```bash
git clone https://github.com/sarrmouhamed29/explorai.git
cd explorai
cp .env.example infra/.env        # git-ignored; placeholder values for local development only
docker compose --env-file infra/.env -f infra/docker-compose.yml up -d --build --wait
```

Open <http://127.0.0.1:5173>. You should see "Backend ready" and a list with `postgres: ok` and `redis: ok`. The backend is also reachable directly:

```bash
curl http://127.0.0.1:8000/health/ready
# {"status":"ok","checks":{"postgres":"ok","redis":"ok"}}
```

Tear everything down, including the database volume:

```bash
docker compose --env-file infra/.env -f infra/docker-compose.yml down -v
```

Use `127.0.0.1`, not `localhost` (see the Windows notes in [CONTRIBUTING.md](CONTRIBUTING.md#windows-notes)). All published ports are bound to `127.0.0.1` only.

## Install dependencies (for running tests and tools on the host)

```bash
uv sync
pnpm --dir frontend install
```

## Run the tests

Backend unit tests (no services needed):

```bash
uv run --package explorai-backend pytest backend/tests -m "not integration"
```

Backend integration tests need the Compose stack running and the variables from `infra/.env` exported. The exact steps are in [`backend/README.md`](backend/README.md#test).

Each Python package is tested on its own (each has its own configuration and coverage gate); `db` and `pipelines` integration tests need `DATABASE_URL` pointing at a PostgreSQL superuser:

```bash
uv run pytest db/tests
uv run pytest pipelines/tests
```

Details: [`db/README.md`](db/README.md#test) and [`pipelines/README.md`](pipelines/README.md#test).
Frontend tests, type check and lint:

```bash
pnpm --dir frontend test
pnpm --dir frontend typecheck
pnpm --dir frontend lint
```

End-to-end smoke test (builds and starts the whole stack under its own Compose project name, checks the backend, the frontend and the proxied health route, then tears it down):

```bash
bash infra/smoke.sh
SMOKE_NETWORK=1 bash infra/smoke.sh      # also ingests two real papers from arXiv (needs internet)
```

## Notes on configuration

The backend reads its configuration from environment variables only and refuses to start if one is invalid. If the PostgreSQL password contains `@`, `/` or `:`, URL-encode it inside `DATABASE_URL` (for example `@` becomes `%40`); the `POSTGRES_PASSWORD` variable used by the database container keeps the raw value.

## Repository layout

```
backend/            FastAPI service (src/explorai_backend, tests, Dockerfile, openapi.json)
frontend/           Vite + React + TypeScript app, generated API client, Vitest tests, Dockerfile
db/                 shared schema: models, Alembic migrations, tests
pipelines/          ingestion: sources, extraction, cleaning, dedup, Prefect flows, Dockerfile
infra/              docker-compose.yml and smoke.sh
docs/adr/           Architecture Decision Records
docs/concepts/      Concept notes with interview questions and answers
docs/evaluation/    Measured baselines (PDF extraction quality)
docs/superpowers/   Specs and implementation plans of each sub-project
.github/            CI workflow, Dependabot configuration, pull request template
pyproject.toml      uv workspace root (members: backend, db, pipelines)
.pre-commit-config.yaml, .env.example, cliff.toml, CHANGELOG.md
```

## More documentation

- [`plan.md`](plan.md): master plan and architecture decisions
- [`docs/`](docs/): [ADRs](docs/adr/README.md), [concept notes](docs/concepts/00-foundations.md), [ingestion concepts](docs/concepts/01-ingestion.md), specs and plans
- [`CONTRIBUTING.md`](CONTRIBUTING.md): branching, commits, hooks, definition of done, releases
- [`backend/README.md`](backend/README.md), [`frontend/README.md`](frontend/README.md), [`db/README.md`](db/README.md), [`pipelines/README.md`](pipelines/README.md)

## License

MIT, see [`LICENSE`](LICENSE).
