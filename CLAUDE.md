# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

Sub-projects 0 (Foundations), 1 (Ingestion) and 2 (Core backend) are implemented. Foundations is a walking skeleton: a FastAPI backend (`/health/live`, `/health/ready`), a Vite + React + TypeScript page that shows readiness through a client generated from `backend/openapi.json`, a Docker Compose stack (PostgreSQL with pgvector, Redis, backend, frontend), pre-commit hooks and GitHub Actions CI. Ingestion adds `db/` (shared SQLAlchemy models and Alembic migrations) and `pipelines/` (arXiv and web sources, SSRF-safe download, PDF/HTML extraction, cleaning, deduplication, Prefect flows with a nightly schedule), plus `migrate`, `prefect` and `pipelines` Compose services; replaying a batch changes nothing, and `docs/evaluation/extraction-baseline.md` holds the measured PDF extraction baseline. Core backend adds a multi-user API under `/api/v1`: accounts (Argon2id, opaque Redis sessions in an `HttpOnly` cookie, login and upload rate limits, an `Origin` check against CSRF) and documents (keyset-paginated list, read, sections, raw-body PDF upload that is idempotent per owner, delete). Isolation between users is enforced twice: repository owner filters and PostgreSQL Row-Level Security (`explorai_app` role, `scoped_session` with `SET LOCAL ROLE`, `ENABLE` without `FORCE`), proved by `backend/tests/integration/test_isolation.py` and an AST architecture test. The blob store moved into `db/`; the `pipelines` service runs a `process-pending` deployment every 30 seconds (advisory lock, blob garbage collection with a one-hour grace period). Open check: `infra/smoke.sh` and the integration suites of the three packages have not been run against the final code on the maintainer's machine (Docker was unavailable), so run them or let CI before merging. There is no frontend page for login or upload yet (sub-project 6). Sub-project 3 (RAG) is next; `plan.md` is the authoritative master plan and `description.md` is non-binding ideation. Specs and plans live in `docs/superpowers/`; ADRs in `docs/adr/`; concept notes with interview Q&A in `docs/concepts/`.

The maintainer converses in French, but all project artifacts (code, comments, docs, commits, PRs, ADRs) are in English. The maintainer is learning and must be able to justify every choice in interviews: explain choices and alternatives, and explain concepts before implementing them.

## Commands

Run everything from the repository root, in a POSIX shell (Git Bash on Windows). Use `127.0.0.1`, never `localhost`.

```bash
# Install
uv sync
pnpm --dir frontend install

# Backend tests (coverage gate 80% is in backend/pyproject.toml addopts)
uv run --package explorai-backend pytest backend/tests -m "not integration"            # unit only, no services
uv run --package explorai-backend pytest backend/tests/unit/test_health.py::test_live_returns_ok --no-cov   # one test by node id (--no-cov: the 80% gate fails on a partial run)
# Integration tests need the stack and the variables exported (they fail, not skip, without them)
cp .env.example infra/.env
docker compose --env-file infra/.env -f infra/docker-compose.yml up -d --wait
set -a; . infra/.env; set +a
uv run --package explorai-backend pytest backend/tests -m integration

# Python lint, format, types (same as CI)
uv run ruff check backend db pipelines
uv run ruff format backend db pipelines     # CI runs `ruff format --check`
# mypy needs --config-file: run from the root it ignores a package's [tool.mypy]
uv run mypy --config-file backend/pyproject.toml backend/src backend/tests backend/scripts
uv run mypy --config-file db/pyproject.toml db/src db/tests
uv run mypy --config-file pipelines/pyproject.toml pipelines/src pipelines/tests

# db and pipelines: one pytest call per package (own config and 80% gate). Integration tests need
# DATABASE_URL exported (a PostgreSQL superuser; they create and drop throwaway databases).
uv run pytest db/tests
uv run pytest pipelines/tests -m "not integration" --no-cov    # no services
# User-isolation suite alone (needs the stack and the exported variables, like the other integration tests)
uv run --package explorai-backend pytest backend/tests/integration/test_isolation.py --no-cov
uv run python -m explorai_db.migrate                           # apply migrations to DATABASE_URL
uv run python -m explorai_pipelines ingest-arxiv --query cat:cs.CL --max-results 5   # also: ingest-web, reextract, process-pending, serve
uv run python -X utf8 -m explorai_pipelines.measure            # PDF extraction quality on the annotated sample

# Frontend
pnpm --dir frontend test                    # Vitest
pnpm --dir frontend lint
pnpm --dir frontend format                  # CI runs `format:check`
pnpm --dir frontend typecheck
pnpm --dir frontend build

# API contract: after any backend route/schema change, regenerate and commit both files
uv run --package explorai-backend python backend/scripts/export_openapi.py    # backend/openapi.json
pnpm --dir frontend gen:api                                                   # frontend/src/api/schema.d.ts

# Stack and end-to-end smoke test
docker compose --env-file infra/.env -f infra/docker-compose.yml up -d --build --wait
docker compose --env-file infra/.env -f infra/docker-compose.yml down -v
bash infra/smoke.sh                         # own Compose project, builds, checks, tears down
SMOKE_NETWORK=1 bash infra/smoke.sh         # also ingests two real arXiv papers (internet needed)

# Hooks (needs gitleaks v8.30.1, bash and pnpm on PATH)
uv run pre-commit install --install-hooks   # installs pre-commit and commit-msg hooks
uv run pre-commit run --all-files
```

## Decisions (see `plan.md` and `docs/adr/` for detail)

- **Git:** GitFlow. `main` holds tagged releases, `develop` is integration; `feature/*`, `release/*`, `hotfix/*` branches merged by PR with green CI; no direct pushes to `main` or `develop`. Conventional Commits. Remote: `sarrmouhamed29/explorai` on GitHub. Do not change GitHub settings (branch protection, etc.) or push without explicit approval.
- **Stack:** Python + FastAPI backend, React + TypeScript (Vite) frontend, linked by an OpenAPI contract with a generated, committed client (CI fails on drift). Next.js as backend was rejected (the AI ecosystem is Python).
- **Target layout:** `backend/` (thin API), `ai/` (library: `rag/`, `agents/`, `ml/`, `safety/`), `evaluation/`, `pipelines/`, `frontend/`, `infra/`, `docs/`, `notebooks/`, `.github/`, root `uv` workspace. Only `backend/`, `db/`, `pipelines/`, `frontend/`, `infra/`, `docs/` and `.github/` exist today; add the others when their sub-project starts.
- **Boundaries:** `ai/` never depends on `backend/`; `evaluation/` and `pipelines/` reuse `ai/` directly, not through the API. `pipelines/` and `backend/` share the schema, the blob store and the RLS session helper through `db/`, which depends on neither. Request code in `backend/` opens database sessions only through `explorai_db.rls.scoped_session` (an AST test enforces it). Tests live in each package.
- **Cross-cutting:** every sub-project spec has "evaluation criteria" and "threats and guardrails" sections. Evaluation and safety are their own sub-projects, not a final phase.
- **Documentation is part of done:** a change that alters behaviour, architecture or setup updates its docs in the same PR (READMEs, `docs/adr/`, `docs/concepts/`, specs and plans). Significant technical choices get an ADR.
- **Security choices:** opaque sessions over JWT (ADR 0009); RLS as defence in depth (ADR 0010); someone else's document is `404`, a public document on a write is `403`; no password, token, hash or DSN in any response or log; the API never deletes blobs (the pipeline collects them).
- **Tooling:** `uv`, `ruff`, `mypy` strict, `pytest`; `pnpm`, ESLint, Prettier, strict TypeScript, Vitest (TypeScript stays on `^5`: v7 breaks `openapi-typescript`); `pre-commit` (convenience only, CI is the gate); `.env` git-ignored with a versioned `.env.example`; changelog via `git-cliff` (`cliff.toml`), release tags created by hand.

## Workflow

The project is built through the superpowers brainstorming flow: each sub-project in `plan.md` gets its own spec (`docs/superpowers/specs/`), implementation plan and implementation cycle. Do not scaffold a new sub-project or add dependencies before its spec and plan are approved.
