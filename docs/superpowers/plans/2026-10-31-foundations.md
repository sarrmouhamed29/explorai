# Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Explorai walking skeleton: a Docker Compose stack (PostgreSQL, Redis, FastAPI backend, React frontend) whose health is visible in the browser through an OpenAPI-generated client, with tests, hooks, CI and documentation.

**Architecture:** A `uv` workspace holds the `backend/` package (FastAPI, thin). `frontend/` is an independent Vite + React + TypeScript app. They only talk through the OpenAPI contract (`backend/openapi.json` → generated TypeScript types). Dependency checks are injected into the health routes so they can be faked in unit tests and real in integration tests.

**Tech Stack:** Python 3.12, uv, FastAPI, pydantic-settings, psycopg 3, redis-py, pytest, ruff, mypy; Node 22, pnpm, Vite, React, TypeScript, Vitest, msw, openapi-typescript, openapi-fetch; Docker Compose; GitHub Actions; pre-commit, gitleaks.

**Spec:** `docs/superpowers/specs/2026-10-31-foundations-design.md` (also read `plan.md` for tooling rationale).

## Global Constraints

- Everything written in the repo is in English (code, comments, docs, commits, ADRs). The user is learning: each task has a **Concept** line; the matching explanation goes into `docs/concepts/00-foundations.md` (Task 10).
- Git is GitFlow. Work happens on `feature/foundations` cut from `develop`. Commits use Conventional Commits and end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Never push, open a PR or change GitHub settings without the user's explicit approval.
- Routes: `GET /health/live` (always 200) and `GET /health/ready` (200 when PostgreSQL and Redis answer, otherwise 503 with per-dependency status).
- Postgres image `pgvector/pgvector:pg16`; Redis official image; backend and frontend containers run as non-root; published ports bound to `127.0.0.1`; no production credentials in the repo; only `.env.example` is versioned.
- Backend: `mypy` strict, `ruff`, `pytest` with coverage gate at 80%. Frontend: strict TypeScript, ESLint, Prettier, Vitest.
- CI runs on pull requests to `develop`, `main`, `release/*`, `hotfix/*`; jobs: backend, frontend, contract, docker, security.
- Configuration comes only from environment variables, validated at startup.
- Plan-level choices (not in the spec): Python 3.12, Node 22 LTS, Redis 7. Versions are pinned by lockfiles.

## Review Focus

1. A dependency that hangs (not fails): `/health/ready` must answer 503 within the timeout, never hang. (Task 3)
2. Missing or malformed environment variable: startup fails with an error naming the variable. (Task 2)
3. Only Postgres down, or only Redis down: the body marks exactly the failing dependency. (Task 3)
4. Connection strings (with passwords) never appear in responses or logs. (Task 3)
5. Backend unreachable at the network level (not a 503): the page shows "unavailable", not a blank screen. (Task 7)

---

### Task 1: Repository and branch setup

**Files:**
- Create: `.gitignore`, `.env.example`
- Commit existing: `plan.md`, `CLAUDE.md`, `description.md`, `docs/superpowers/**`

**Concept:** GitFlow branches (`main` releases, `develop` integration, `feature/*`).

- [ ] **Step 1: Verify tooling.** Run `git --version`, `docker --version`, `docker compose version`, `uv --version`, `node --version`, `pnpm --version`. Expected: all succeed, Node ≥ 22. Install anything missing before continuing (ask the user for installs that need admin rights).
- [ ] **Step 2: Create branches locally.** `git checkout -b develop main` then `git checkout -b feature/foundations`. Do not push.
- [ ] **Step 3: Write `.gitignore`** (Python, Node, `.env`, `.venv`, `node_modules`, coverage output, Docker overrides) and **`.env.example`** with `APP_ENV`, `DATABASE_URL`, `REDIS_URL`, `CHECK_TIMEOUT_SECONDS`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` using obvious dev placeholders.
- [ ] **Step 4: Commit** the existing docs and these files: `docs: add master plan, foundations spec and plan`.

---

### Task 2: Backend package and configuration

**Files:**
- Create: `pyproject.toml` (workspace root, members `["backend"]`), `backend/pyproject.toml`, `backend/src/explorai_backend/__init__.py`, `backend/src/explorai_backend/config.py`
- Test: `backend/tests/unit/test_config.py`

**Interfaces:**
- Produces: `Settings(BaseSettings)` with `app_env: Literal["development","test","production"]`, `database_url: PostgresDsn`, `redis_url: RedisDsn`, `check_timeout_seconds: float = 2.0`; `get_settings() -> Settings` (cached).

**Concept:** 12-factor configuration; fail fast at startup.

- [ ] **Step 1: Write failing tests** in `test_config.py`:
  - `test_settings_loads_from_environment`: with `DATABASE_URL=postgresql://u:p@localhost:5432/db`, `REDIS_URL=redis://localhost:6379/0`, `APP_ENV=test`, assert `settings.app_env == "test"` and the URLs round-trip.
  - `test_missing_database_url_raises_and_names_variable`: no `DATABASE_URL` → `ValidationError` whose text contains `database_url`.
  - `test_non_postgres_url_is_rejected`: `DATABASE_URL=mysql://...` → `ValidationError`.
  - `test_invalid_app_env_is_rejected`.
- [ ] **Step 2: Run** `uv run --package explorai-backend pytest backend/tests/unit/test_config.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `config.py` and the two `pyproject.toml` files (dependencies: fastapi, uvicorn, pydantic-settings, psycopg[binary], redis; dev: pytest, pytest-cov, pytest-asyncio, httpx, ruff, mypy). Pytest config: `--cov=explorai_backend --cov-fail-under=80`, marker `integration`.
- [ ] **Step 4: Run the same command.** Expected: 4 PASS.
- [ ] **Step 5: Commit** `feat(backend): add validated environment configuration`.

---

### Task 3: Health routes with injectable dependency checks

**Files:**
- Create: `backend/src/explorai_backend/db.py`, `backend/src/explorai_backend/api/__init__.py`, `backend/src/explorai_backend/api/health.py`, `backend/src/explorai_backend/main.py`, `backend/README.md`
- Test: `backend/tests/unit/test_health.py`

**Interfaces:**
- Consumes: `Settings`, `get_settings()` from Task 2.
- Produces:
  - `db.py`: `async def check_postgres(database_url: str) -> bool` and `async def check_redis(redis_url: str) -> bool`; they never raise, returning `False` on any error.
  - `health.py`: `router` (prefix `/health`); `get_checks(settings) -> dict[str, Callable[[], Awaitable[bool]]]` as a FastAPI dependency (keys `postgres`, `redis`); response models `LiveResponse(status)` and `ReadyResponse(status, checks: dict[str, Literal["ok","down"]])`; `/ready` documents `503: ReadyResponse`.
  - `main.py`: `create_app() -> FastAPI` and module-level `app`.

**Concept:** liveness vs readiness; dependency injection for testability.

- [ ] **Step 1: Write failing tests** in `test_health.py` using `TestClient` and `app.dependency_overrides[get_checks]`:
  - `test_live_returns_ok`: 200, `{"status": "ok"}`.
  - `test_ready_all_healthy`: 200, `checks == {"postgres": "ok", "redis": "ok"}`.
  - `test_ready_postgres_down_marks_only_postgres`: 503, `checks == {"postgres": "down", "redis": "ok"}`.
  - `test_ready_redis_down_marks_only_redis`: 503, mirror case.
  - `test_ready_hanging_check_returns_503_within_timeout`: a check that sleeps 10 s, `check_timeout_seconds=0.2`, assert 503 and elapsed < 2 s.
  - `test_ready_check_raising_is_reported_down`: a check raising `RuntimeError("secret://pw")` → 503, and the response text does not contain `secret://pw`.
- [ ] **Step 2: Run** `uv run --package explorai-backend pytest backend/tests/unit/test_health.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the modules. Approach: `/ready` runs checks concurrently with `asyncio.gather`, each wrapped in `asyncio.wait_for(..., settings.check_timeout_seconds)` and a catch-all that maps timeout or exception to `False`; no exception text reaches the response. `db.py` uses `psycopg.AsyncConnection.connect` + `SELECT 1` and `redis.asyncio.from_url(...).ping()`. Write `backend/README.md` (purpose, how to run, test, env vars).
- [ ] **Step 4: Run** the unit suite. Expected: all PASS, coverage ≥ 80%.
- [ ] **Step 5: Commit** `feat(backend): add liveness and readiness routes`.

---

### Task 4: Integration tests against real dependencies

**Files:**
- Create: `infra/docker-compose.yml` (postgres and redis services only at this stage, with healthchecks, ports on `127.0.0.1`)
- Test: `backend/tests/integration/test_connectivity.py` (marker `integration`)

**Interfaces:**
- Consumes: `check_postgres`, `check_redis`, `create_app` (Task 3).

**Concept:** why integration tests exist next to fakes.

- [ ] **Step 1: Write failing tests**:
  - `test_check_postgres_true_for_real_server`, `test_check_redis_true_for_real_server` (URLs from environment).
  - `test_check_postgres_false_when_port_closed`: `postgresql://u:p@127.0.0.1:1/db` → `False`.
  - `test_check_redis_false_when_port_closed`.
  - `test_ready_endpoint_with_real_dependencies_returns_200`.
- [ ] **Step 2:** `docker compose -f infra/docker-compose.yml up -d --wait postgres redis`, then `uv run --package explorai-backend pytest -m integration -v`. Expected on first run: closed-port tests may already pass; real-server tests PASS once Compose is healthy (record any FAIL and fix configuration, not the test).
- [ ] **Step 3: Stop the stack** and re-run the same tests. Expected: real-server tests FAIL, proving they test something.
- [ ] **Step 4: Commit** `test(backend): add integration tests with real Postgres and Redis`.

---

### Task 5: OpenAPI contract export

**Files:**
- Create: `backend/scripts/export_openapi.py`, `backend/openapi.json` (generated, versioned)
- Test: `backend/tests/unit/test_openapi_contract.py`

**Interfaces:**
- Produces: `backend/openapi.json`; command `uv run --package explorai-backend python backend/scripts/export_openapi.py` rewrites it.

**Concept:** the API contract as the only coupling between services.

- [ ] **Step 1: Write failing tests**: `test_committed_openapi_matches_app` (parsed file equals `create_app().openapi()`), `test_openapi_declares_health_paths` (both paths present, `/health/ready` declares 200 and 503).
- [ ] **Step 2: Run.** Expected: FAIL (file missing).
- [ ] **Step 3: Implement** the export script (deterministic JSON: sorted keys, 2-space indent, trailing newline); run it.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(backend): export OpenAPI contract`.

---

### Task 6: Backend container and Compose stack

**Files:**
- Create: `backend/Dockerfile`, `.dockerignore`
- Modify: `infra/docker-compose.yml` (add `backend`)

**Concept:** multi-stage builds, non-root containers, `depends_on` with health conditions.

- [ ] **Step 1: Write `backend/Dockerfile`**: multi-stage with uv, final stage runs as a non-root user, command `uvicorn explorai_backend.main:app --host 0.0.0.0 --port 8000`, container `HEALTHCHECK` on `/health/live`.
- [ ] **Step 2: Add the `backend` service**: env from `infra/.env` (copied from `.env.example`, git-ignored), `depends_on` postgres and redis with `condition: service_healthy`, port `127.0.0.1:8000:8000`.
- [ ] **Step 3: Verify.** `docker compose -f infra/docker-compose.yml up -d --build --wait`, then `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/health/ready`. Expected: `200`. Then `docker compose ... stop postgres` and the same curl. Expected: `503`. Also `docker compose exec backend id -u` must not print `0`.
- [ ] **Step 4: Commit** `build(infra): containerize the backend and add it to Compose`.

---

### Task 7: Frontend with generated API client

**Files:**
- Create: `frontend/` (Vite React TS scaffold via pnpm), `frontend/src/api/client.ts`, `frontend/src/api/schema.d.ts` (generated, versioned), `frontend/src/hooks/useBackendHealth.ts`, `frontend/src/components/HealthStatus.tsx`, `frontend/README.md`
- Test: `frontend/src/components/HealthStatus.test.tsx`
- Modify: `frontend/vite.config.ts` (dev proxy of `/health` to `BACKEND_URL`, which avoids CORS in development), `frontend/package.json` (scripts `gen:api`, `typecheck`, `lint`, `test`, `build`)

**Interfaces:**
- Consumes: `backend/openapi.json` (Task 5).
- Produces: `useBackendHealth(): { state: "loading" | "ready" | "unavailable"; checks?: Record<string, "ok" | "down"> }`; `<HealthStatus />`.

**Concept:** generated types remove hand-written drift; why a dev proxy.

- [ ] **Step 1: Scaffold** the app, add `openapi-typescript`, `openapi-fetch`, `vitest`, `@testing-library/react`, `msw`; implement `gen:api` as `openapi-typescript ../backend/openapi.json -o src/api/schema.d.ts` and run it.
- [ ] **Step 2: Write failing tests** with msw handlers:
  - `shows "Backend ready" and each dependency as ok when /health/ready returns 200`.
  - `shows "Backend unavailable" and marks postgres down when it returns 503 with postgres down`.
  - `shows "Backend unavailable" when the request fails at network level`.
  - `shows a loading state before the first response`.
- [ ] **Step 3: Run** `pnpm --dir frontend test`. Expected: FAIL.
- [ ] **Step 4: Implement** the hook (calls the client's `GET /health/ready`, treats 503 bodies as data and network errors as `unavailable`) and the component; render it from `App.tsx`.
- [ ] **Step 5: Run** `pnpm --dir frontend test && pnpm --dir frontend typecheck && pnpm --dir frontend build`. Expected: all pass.
- [ ] **Step 6: Commit** `feat(frontend): add health status page using the generated client`.

---

### Task 8: Frontend in Compose and end-to-end smoke test

**Files:**
- Create: `frontend/Dockerfile` (dev image, non-root), `infra/smoke.sh`
- Modify: `infra/docker-compose.yml` (add `frontend`, `BACKEND_URL=http://backend:8000`, port `127.0.0.1:5173:5173`)

**Concept:** smoke tests prove the assembled system, not the parts.

- [ ] **Step 1: Write `infra/smoke.sh`** (bash, `set -euo pipefail`): `docker compose up -d --build --wait`; assert `/health/ready` returns 200 on port 8000; assert `http://127.0.0.1:5173/` returns 200 and the proxied `http://127.0.0.1:5173/health/ready` returns 200; always `docker compose down -v` on exit.
- [ ] **Step 2: Run** `bash infra/smoke.sh`. Expected: exits 0 and prints each check as passed.
- [ ] **Step 3: Open** `http://127.0.0.1:5173` in the browser pane and confirm "Backend ready"; stop Redis and confirm it switches to "Backend unavailable".
- [ ] **Step 4: Commit** `build(infra): add frontend to Compose and end-to-end smoke test`.

---

### Task 9: Quality tooling and local hooks

**Files:**
- Create/Modify: `.pre-commit-config.yaml`, `ruff` and `mypy` sections in `backend/pyproject.toml`, `frontend/eslint.config.js`, `frontend/.prettierrc`, `.gitleaks.toml` (only if defaults need tuning)

**Concept:** shift-left checks; hooks are convenience, CI is the gate.

- [ ] **Step 1: Configure** `ruff` (lint + format), `mypy` with `strict = true`, ESLint (react-hooks rules) and Prettier. Fix what they flag in existing code.
- [ ] **Step 2: Verify** `uv run ruff check . && uv run ruff format --check . && uv run mypy backend/src` and `pnpm --dir frontend lint`. Expected: clean.
- [ ] **Step 3: Configure `pre-commit`** with ruff, Prettier/ESLint on changed files, gitleaks, and a Conventional Commits `commit-msg` hook; `uv run pre-commit install --install-hooks --hook-type pre-commit --hook-type commit-msg`.
- [ ] **Step 4: Prove the hooks work.** Stage a file containing a fake AWS key (`AKIAIOSFODNN7EXAMPLE` pattern) → commit is rejected; commit with message `bad message` → rejected; remove the file afterwards.
- [ ] **Step 5: Commit** `chore: add linting, typing and pre-commit hooks`.

---

### Task 10: CI workflows, templates and documentation

**Files:**
- Create: `.github/workflows/ci.yml`, `.github/dependabot.yml`, `.github/pull_request_template.md`, `README.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, `cliff.toml`, `docs/adr/0001-gitflow.md`, `0002-fastapi-over-nextjs-backend.md`, `0003-monorepo-independent-services.md`, `0004-uv-workspace.md`, `0005-ai-library-independent-of-backend.md`, `docs/concepts/00-foundations.md`

**Concept:** required checks as the real quality gate; ADRs record the *why*.

- [ ] **Step 1: Write `ci.yml`** triggered on pull requests to `develop`, `main`, `release/*`, `hotfix/*` with jobs: `backend` (ruff, mypy, pytest with Postgres and Redis service containers, coverage gate), `frontend` (lint, typecheck, test, build), `contract` (run `export_openapi.py` and `pnpm gen:api`, then `git diff --exit-code`), `docker` (build images and run `infra/smoke.sh`), `security` (gitleaks, `pip-audit`, `pnpm audit`). Use dependency caches.
- [ ] **Step 2: Write `dependabot.yml`** (pip/uv, npm, github-actions, docker; weekly) and the PR template (checklist including "docs updated").
- [ ] **Step 3: Write the documentation**: root `README.md` (quick start), `CONTRIBUTING.md` (GitFlow flow, commit format, definition of done including docs), the five ADRs (context, decision, alternatives, consequences, taken from `plan.md`), `cliff.toml` and an initial `CHANGELOG.md` generated with `git-cliff`, and `docs/concepts/00-foundations.md` covering 12-factor config, liveness vs readiness, the test pyramid, the API contract, non-root containers, with at least 8 interview questions and model answers.
- [ ] **Step 4: Validate locally**: YAML parses (`uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"`), every README command runs as written, every doc listed in spec section 8 exists.
- [ ] **Step 5: Commit** `ci: add GitHub Actions workflows`, then `docs: add contributing guide, ADRs and foundations concepts`.
- [ ] **Step 6 (needs explicit user approval): Prove CI.** Push `develop` and `feature/foundations`, open a PR to `develop`, and confirm all jobs are green. Then, on throwaway branches, confirm CI fails on (a) a planted fake secret and (b) an edited `openapi.json` that no longer matches the app. Report results with run links. Branch protection is offered to the user afterwards, never applied unasked.

---

## Self-review notes

- Spec coverage: scope items → Tasks 2–8; tooling and CI (spec 7) → Tasks 9–10; documentation (spec 8) → Tasks 3, 7, 10; threats (spec 9) → Tasks 6, 9, 10 and Review Focus; definition of done (spec 10) → Task 8 step 2–3 (items 1–3), Task 10 step 6 (item 4), Task 10 step 4 (item 5).
- Interface names are consistent across tasks (`get_checks`, `check_postgres`, `check_redis`, `create_app`, `useBackendHealth`).
