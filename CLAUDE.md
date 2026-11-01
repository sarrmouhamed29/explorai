# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

Sub-projects 0 (Foundations), 1 (Ingestion), 2 (Core backend) and 3 (RAG) are implemented. Foundations is a walking skeleton: a FastAPI backend (`/health/live`, `/health/ready`), a Vite + React + TypeScript page that shows readiness through a client generated from `backend/openapi.json`, a Docker Compose stack (PostgreSQL with pgvector, Redis, backend, frontend), pre-commit hooks and GitHub Actions CI. Ingestion adds `db/` (shared SQLAlchemy models and Alembic migrations) and `pipelines/` (arXiv and web sources, SSRF-safe download, PDF/HTML extraction, cleaning, deduplication, Prefect flows with a nightly schedule), plus `migrate`, `prefect` and `pipelines` Compose services; replaying a batch changes nothing, and `docs/evaluation/extraction-baseline.md` holds the measured PDF extraction baseline. Core backend adds a multi-user API under `/api/v1`: accounts (Argon2id, opaque Redis sessions in an `HttpOnly` cookie, login and upload rate limits, an `Origin` check against CSRF) and documents (keyset-paginated list, read, sections, raw-body PDF upload that is idempotent per owner, delete). Isolation between users is enforced twice: repository owner filters and PostgreSQL Row-Level Security (`explorai_app` role, `scoped_session` with `SET LOCAL ROLE`, `ENABLE` without `FORCE`), proved by `backend/tests/integration/test_isolation.py` and an AST architecture test. The blob store moved into `db/`; the `pipelines` service runs a `process-pending` deployment every 30 seconds (advisory lock, blob garbage collection with a one-hour grace period). Open check: `infra/smoke.sh` and the integration suites of the three packages have not been run against the final code on the maintainer's machine (Docker was unavailable), so run them or let CI before merging. RAG (branch `feature/rag`) adds `ai/` (`explorai_ai`, depends on `db/` only): chunking (400-word windows, 60 overlap), a local sparse encoder, provider ports with deterministic fakes and Voyage/Claude adapters (`RAG_PROVIDER=fake|real`), a Qdrant adapter (collection per embedding model and dimension, alias `chunks_current`, tenant index on `owner_id`, no text in payloads), `Retriever` (rewrite, hybrid RRF search under the single `scope.build_scope_filter`, re-read under RLS through `ChunkStore`, rerank), verified citations (2 words and 10 normalised characters minimum) and three refusal levels; migration 0005 (`chunks`, `chunk_index_entries` keyed by `index_name`, RLS); `index-pending` and `prune-index` deployments in `pipelines/`; `POST /api/v1/ask` (30/h, reservation given back on any unserved outcome) and an `indexed` flag in `backend/`; a `qdrant` Compose and CI service; an on-demand measurement (`explorai_ai.measure`, 14 annotated questions in `ai/tests/fixtures/questions/`). Open checks for RAG: Docker was unresponsive during the whole sub-project, so every integration suite of Tasks 3-10 (including `backend/tests/integration/test_rag_isolation.py` and its mutation proof), `infra/smoke.sh`, the image builds and the Qdrant health check have never run: CI is their first run and nothing merges before it is green. The real providers have never been called (Voyage model names and limits came from a summarising fetch: re-verify before the first real call), the annotated questions are unvalidated by the maintainer and no real measurement exists. There is no frontend page for login, upload or chat yet (sub-project 6). Sub-project 4 (Evaluation) is next; `plan.md` is the authoritative master plan and `description.md` is non-binding ideation. Specs and plans live in `docs/superpowers/`; ADRs in `docs/adr/`; concept notes with interview Q&A in `docs/concepts/`.

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
uv run ruff check backend db pipelines ai
uv run ruff format backend db pipelines ai  # CI runs `ruff format --check`
# mypy needs --config-file: run from the root it ignores a package's [tool.mypy]
uv run mypy --config-file backend/pyproject.toml backend/src backend/tests backend/scripts
uv run mypy --config-file db/pyproject.toml db/src db/tests
uv run mypy --config-file pipelines/pyproject.toml pipelines/src pipelines/tests
uv run mypy --config-file ai/pyproject.toml ai/src ai/tests

# db and pipelines: one pytest call per package (own config and 80% gate). Integration tests need
# DATABASE_URL exported (a PostgreSQL superuser; they create and drop throwaway databases).
uv run pytest db/tests
uv run pytest pipelines/tests -m "not integration" --no-cov    # no services
# User-isolation suites alone (need the stack and the exported variables, like the other integration tests)
uv run --package explorai-backend pytest backend/tests/integration/test_isolation.py --no-cov
uv run --package explorai-backend pytest backend/tests/integration/test_rag_isolation.py --no-cov
uv run python -m explorai_db.migrate                           # apply migrations to DATABASE_URL
uv run python -m explorai_pipelines ingest-arxiv --query cat:cs.CL --max-results 5   # also: ingest-web, reextract, process-pending, serve
uv run python -X utf8 -m explorai_pipelines.measure            # PDF extraction quality on the annotated sample
uv run python -m explorai_pipelines index-pending              # one indexing pass (needs RAG_PROVIDER, Qdrant); also prune-index

# ai: same pattern (own config and 80% gate). Integration tests need DATABASE_URL (superuser) and QDRANT_URL/QDRANT_API_KEY.
uv run pytest ai/tests -m "not integration" --no-cov           # no services, no keys (fake providers)
uv run pytest ai/tests
uv run python -X utf8 -m explorai_ai.measure --provider fake   # plumbing check only; --provider real needs the maintainer's keys

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
- **Target layout:** `backend/` (thin API), `ai/` (library: `rag/`, `agents/`, `ml/`, `safety/`), `evaluation/`, `pipelines/`, `frontend/`, `infra/`, `docs/`, `notebooks/`, `.github/`, root `uv` workspace. Only `backend/`, `db/`, `pipelines/`, `ai/`, `frontend/`, `infra/`, `docs/` and `.github/` exist today; add the others when their sub-project starts. `ai/` is flat for now (`explorai_ai.retriever`, not `explorai_ai.rag.retriever`; see the status note of ADR 0005).
- **Boundaries:** `ai/` never depends on `backend/`; `evaluation/` and `pipelines/` reuse `ai/` directly, not through the API. `pipelines/` and `backend/` share the schema, the blob store and the RLS session helper through `db/`, which depends on neither. Request code in `backend/` opens database sessions only through `explorai_db.rls.scoped_session` (an AST test enforces it). `ai/` depends on `db/` only; `backend/` and `pipelines/` depend on `ai/`. Only `explorai_ai.scope.build_scope_filter` builds Qdrant filters (AST test), and Qdrant never holds text: chunk texts are always re-read under RLS (`ChunkStore.hydrate`). Tests live in each package.
- **Cross-cutting:** every sub-project spec has "evaluation criteria" and "threats and guardrails" sections. Evaluation and safety are their own sub-projects, not a final phase.
- **Documentation is part of done:** a change that alters behaviour, architecture or setup updates its docs in the same PR (READMEs, `docs/adr/`, `docs/concepts/`, specs and plans). Significant technical choices get an ADR.
- **Security choices:** opaque sessions over JWT (ADR 0009); RLS as defence in depth (ADR 0010); Qdrant chosen over the recommended pgvector, with isolation in two layers (ADR 0011); no RAG framework (ADR 0012); citations verified by code (ADR 0013); providers behind ports with fakes, tests never use the network or a key, and private text sent to Voyage and Anthropic is a documented limitation (ADR 0014); someone else's document is `404`, a public document on a write is `403`; no password, token, hash or DSN in any response or log; the API never deletes blobs (the pipeline collects them).
- **Tooling:** `uv`, `ruff`, `mypy` strict, `pytest`; `pnpm`, ESLint, Prettier, strict TypeScript, Vitest (TypeScript stays on `^5`: v7 breaks `openapi-typescript`); `pre-commit` (convenience only, CI is the gate); `.env` git-ignored with a versioned `.env.example`; changelog via `git-cliff` (`cliff.toml`), release tags created by hand.

## Workflow

The project is built through the superpowers brainstorming flow: each sub-project in `plan.md` gets its own spec (`docs/superpowers/specs/`), implementation plan and implementation cycle. Do not scaffold a new sub-project or add dependencies before its spec and plan are approved.
