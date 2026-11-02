# Explorai

Explorai is an AI-powered research and decision intelligence platform. The long-term goal is to let a user upload documents, ask questions answered with verifiable citations (RAG), run research agents, apply analytics and ML, and receive scheduled reports. It is also a learning project: every technical choice is documented with the alternatives that were rejected (see `docs/adr/` and `plan.md`).

## Status

Sub-project 0, **Foundations**, is implemented as a walking skeleton: the smallest version of the system that crosses every layer, with no business feature yet.

- Backend: FastAPI service with `GET /health/live` and `GET /health/ready` (checks PostgreSQL and Redis).
- Frontend: one React page that shows the backend readiness, through a TypeScript client generated from the backend's OpenAPI contract.
- Docker Compose stack: PostgreSQL (pgvector image), Redis, backend, frontend (and, since ingestion, `migrate`, `prefect` and `pipelines`; since RAG, `qdrant`).
- Tests, pre-commit hooks and GitHub Actions CI.

Sub-project 1, **Ingestion**, is implemented on top of it: a pipeline that collects arXiv papers and web pages, extracts and cleans their text, marks duplicates and stores versioned text in PostgreSQL.

- `db/`: the shared schema (SQLAlchemy models, Alembic migrations).
- `pipelines/`: sources, safe download, PDF and HTML extraction, cleaning, deduplication, Prefect flows and a nightly schedule. Replaying a batch changes nothing.
- Compose gains a one-shot `migrate` service, a Prefect server (<http://127.0.0.1:4200>) and the `pipelines` service.
- Measured baseline for PDF extraction: [`docs/evaluation/extraction-baseline.md`](docs/evaluation/extraction-baseline.md).

Sub-project 2, **Core backend**, is implemented on top of both: a multi-user API under `/api/v1`.

- Accounts (Argon2id passwords, opaque Redis sessions in an `HttpOnly` cookie, login rate limits) and documents (list with keyset pagination, read, sections, private PDF upload, delete).
- Isolation between users in two layers, repository filters and PostgreSQL Row-Level Security, proved by an automated suite over every route.
- Uploads are processed by the pipelines service every 30 seconds; the blob store now lives in `db/`.
- Concepts and interview Q&A: [`docs/concepts/02-core-backend.md`](docs/concepts/02-core-backend.md); API and `curl` examples: [`backend/README.md`](backend/README.md#api).
- The end-to-end smoke test and the integration suites were written but have not yet been run against this code on the maintainer's machine (Docker was unavailable); run them, or let CI, before merging.

Sub-project 3, **RAG**, is implemented on top of them: questions answered from the documents a user may read, with citations checked by code.

- `ai/` (`explorai_ai`): chunking, a local sparse encoder, hybrid search in Qdrant (dense and sparse vectors fused by RRF), re-reading of chunk texts from PostgreSQL under Row-Level Security, reranking, optional query rewriting, answers from Claude through a forced tool, and verification of every quote against its source. Three refusal levels give `insufficient_evidence` when the corpus does not support an answer.
- `POST /api/v1/ask` (30 questions per hour per user) and an `indexed` flag on documents; the pipelines service gains the `index-pending` (30 s) and `prune-index` (hourly) deployments; Compose and CI gain a `qdrant` service.
- Isolation holds down to retrieval in two layers: one function builds every Qdrant filter, and Qdrant holds no text, so everything shown is re-read under RLS.
- Every test, CI and the dev stack use deterministic fake models (`RAG_PROVIDER=fake`): no network, no API key. With `RAG_PROVIDER=real`, private text is sent to Voyage AI and Anthropic.
- Concepts and interview Q&A: [`docs/concepts/03-rag.md`](docs/concepts/03-rag.md); choices: ADRs [0011](docs/adr/0011-qdrant-and-retrieval-isolation.md) to [0014](docs/adr/0014-providers-behind-interfaces.md); quality method: [`docs/evaluation/rag-baseline.md`](docs/evaluation/rag-baseline.md).
- **Open checks before merging:** Docker was unresponsive during the whole sub-project, so the integration suites of Tasks 3 to 10 (including the search-isolation suite and its mutation proof), the smoke test, the image builds and the Qdrant health check have **never run**; CI is their first run, and nothing merges before it is green. The real providers have never been called, and the annotated questions are not yet validated by the maintainer: no quality number exists.

There is still no login, upload or chat page in the frontend (sub-project 6). Sub-project 4 (Evaluation) is next. The full roadmap is in [`plan.md`](plan.md).

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

Backend integration tests (including the user-isolation suite) need the Compose stack running and the variables from `infra/.env` exported. The exact steps are in [`backend/README.md`](backend/README.md#test).

Each Python package is tested on its own (each has its own configuration and coverage gate); `db`, `pipelines` and `ai` integration tests need `DATABASE_URL` pointing at a PostgreSQL superuser, and `pipelines` and `ai` also `QDRANT_URL` and `QDRANT_API_KEY`:

```bash
uv run pytest db/tests
uv run pytest pipelines/tests
uv run pytest ai/tests
uv run pytest ai/tests -m "not integration" --no-cov     # no services
```

Details: [`db/README.md`](db/README.md#test), [`pipelines/README.md`](pipelines/README.md#test) and [`ai/README.md`](ai/README.md#test-and-check).
Frontend tests, type check and lint:

```bash
pnpm --dir frontend test
pnpm --dir frontend typecheck
pnpm --dir frontend lint
```

End-to-end smoke test (builds and starts the whole stack under its own Compose project name, checks the backend, the frontend and the proxied health route, then signs up two users, uploads a PDF, waits for the pipeline to process and index it, asks a question that must be answered with citations and a nonsense question that must be refused, checks that the second user gets a 404 on the document and on a question about it, then tears it down; allow up to a few minutes; fake models, no key):

```bash
bash infra/smoke.sh
SMOKE_NETWORK=1 bash infra/smoke.sh      # also ingests two real papers from arXiv (needs internet)
```

## Notes on configuration

The backend reads its configuration from environment variables only and refuses to start if one is invalid. If the PostgreSQL password contains `@`, `/` or `:`, URL-encode it inside `DATABASE_URL` (for example `@` becomes `%40`); the `POSTGRES_PASSWORD` variable used by the database container keeps the raw value.

## Repository layout

```
backend/            FastAPI service: auth, documents and ask API (src/explorai_backend, tests, Dockerfile, openapi.json)
ai/                 RAG library: chunking, providers and fakes, Qdrant index, retriever, citations, measurement
frontend/           Vite + React + TypeScript app, generated API client, Vitest tests, Dockerfile
db/                 shared persistence: models, Alembic migrations, RLS session, blob store, tests
pipelines/          ingestion and indexing: sources, extraction, cleaning, dedup, chunk indexing, Prefect flows, Dockerfile
infra/              docker-compose.yml and smoke.sh
docs/adr/           Architecture Decision Records
docs/concepts/      Concept notes with interview questions and answers
docs/evaluation/    Measured baselines (PDF extraction quality) and the RAG measurement method
docs/superpowers/   Specs and implementation plans of each sub-project
.github/            CI workflow, Dependabot configuration, pull request template
pyproject.toml      uv workspace root (members: backend, db, pipelines, ai)
.pre-commit-config.yaml, .env.example, cliff.toml, CHANGELOG.md
```

## More documentation

- [`plan.md`](plan.md): master plan and architecture decisions
- [`docs/`](docs/): [ADRs](docs/adr/README.md), [concept notes](docs/concepts/00-foundations.md), [ingestion concepts](docs/concepts/01-ingestion.md), [core backend concepts](docs/concepts/02-core-backend.md), [RAG concepts](docs/concepts/03-rag.md), specs and plans
- [`CONTRIBUTING.md`](CONTRIBUTING.md): branching, commits, hooks, definition of done, releases
- [`backend/README.md`](backend/README.md), [`frontend/README.md`](frontend/README.md), [`db/README.md`](db/README.md), [`pipelines/README.md`](pipelines/README.md), [`ai/README.md`](ai/README.md)

## License

MIT, see [`LICENSE`](LICENSE).
