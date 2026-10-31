# Sub-project 0: Foundations, design spec

Part of the master plan (`plan.md`). Date: 2026-10-31. Status: draft, awaiting review.

## 1. Goal

Deliver a **walking skeleton**: the smallest version of Explorai that crosses every layer end to end, with no business feature. It proves that the repository, containers, API, database, cache, API contract, frontend, tests, CI and documentation work together before any AI complexity is added.

Success means a developer can clone the repo, run one command, and get a healthy stack whose state is visible in the browser, with CI enforcing quality on every pull request.

## 2. Scope

**In scope**
- Monorepo skeleton with independent `backend/` and `frontend/` services and a root `uv` workspace.
- Docker Compose stack: PostgreSQL (pgvector-enabled image), Redis, backend, frontend.
- Backend: configuration, database and Redis connectivity checks, liveness and readiness routes.
- Frontend: one page showing the backend state through a client generated from the OpenAPI contract.
- Tests at unit, integration, frontend and end-to-end smoke levels.
- Local hooks (`pre-commit`) and GitHub Actions CI.
- Documentation skeleton and the first ADRs.

**Out of scope** (created when their sub-project starts): `ai/`, `pipelines/`, `evaluation/`, authentication, data models and migrations, vector search, production images, cloud deployment, GitHub branch protection (applied only with explicit approval).

## 3. Repository layout at the end of this sub-project

```
explorai/
├── backend/
│   ├── src/explorai_backend/    main.py, config.py, db.py, api/health.py
│   ├── tests/
│   ├── pyproject.toml, Dockerfile, README.md
├── frontend/                    Vite + React + TypeScript, generated API client, Vitest
├── infra/                       docker-compose and environment files
├── docs/                        adr/, concepts/, superpowers/specs/
├── .github/                     CI workflows, PR and issue templates, Dependabot config
├── pyproject.toml               uv workspace root
├── .pre-commit-config.yaml, .env.example, CONTRIBUTING.md, CHANGELOG.md, README.md
```

Design choices:
- `src/` layout for the backend so code cannot be imported without being installed, which prevents tests that pass by accident.
- Configuration with `pydantic-settings`: everything comes from environment variables and is validated at startup; the app refuses to start on invalid configuration (12-factor).
- Packages are added to the workspace only when their sub-project begins (YAGNI).

## 4. Runtime

`docker compose up` starts four services, each with a healthcheck:

| Service | Image / source | Notes |
|---|---|---|
| postgres | `pgvector/pgvector` | pgvector available so the later vector-store decision is not blocked |
| redis | official `redis` image | |
| backend | `backend/Dockerfile` | waits for postgres and redis to be healthy |
| frontend | Vite dev server | development mode only; production image belongs to sub-project 10 |

Backend routes:
- `GET /health/live` (liveness): always 200 while the process answers.
- `GET /health/ready` (readiness): 200 when PostgreSQL and Redis respond, 503 otherwise, with per-dependency status in the body.

Rationale: an orchestrator restarts a container that is not live, but only stops routing traffic to one that is not ready.

## 5. API contract

The backend exports `openapi.json`. The frontend generates TypeScript types with `openapi-typescript` and calls the API through `openapi-fetch`. The generated client is versioned; CI regenerates it and fails if it differs from the committed one (drift detection).

## 6. Testing strategy

- **Unit (backend):** health routes with fake dependencies via FastAPI dependency overrides. Cases: all healthy gives 200; database down gives 503; Redis down gives 503.
- **Integration (backend):** same routes against real PostgreSQL and Redis provided as CI service containers.
- **Frontend:** Vitest and Testing Library with `msw`; the page shows "ready" or "unavailable" accordingly.
- **End-to-end smoke:** in CI, `docker compose up`, wait for healthchecks, call `/health/ready`, load the frontend page.
- Backend coverage gate at 80% as a guardrail, not a goal.

## 7. Tooling and automation

Choices and rejected alternatives are listed in `plan.md` (Tooling).

**Local hooks (`pre-commit`):** `ruff`, ESLint and Prettier on changed files, `gitleaks`, Conventional Commits message check. `mypy` runs in CI only because it is too slow per commit. Hooks are a convenience and can be bypassed; CI is the gate.

**GitHub Actions**, on pull requests to `develop`, `main`, `release/*`, `hotfix/*`:

| Job | Content |
|---|---|
| backend | `ruff`, `mypy` strict, `pytest` with coverage |
| frontend | ESLint, `tsc`, Vitest, build |
| contract | regenerate the client, fail on drift |
| docker | build images, compose smoke test |
| security | `gitleaks`, `pip-audit`, `pnpm audit` |

Dependabot opens dependency update PRs. Caches speed up CI. Making these checks required is done through branch protection, which is configured only after explicit approval.

## 8. Documentation deliverables

- Root `README.md` with quick start, and a README per service.
- `CONTRIBUTING.md`: GitFlow workflow, Conventional Commits, definition of done (documentation included).
- ADRs in `docs/adr/` for: GitFlow, FastAPI over Next.js as backend, monorepo with independent services, `uv`, package separation (`ai/` independent of `backend/`).
- `docs/concepts/00-foundations.md`: 12-factor configuration, liveness vs readiness, test pyramid, API contract, with interview questions and answers, in English.

## 9. Threats and guardrails

| Threat | Guardrail |
|---|---|
| Secrets committed to git | `gitleaks` in hook and CI; `.env` git-ignored; only `.env.example` versioned |
| Vulnerable dependencies | lockfiles, `pip-audit`, `pnpm audit`, Dependabot |
| Insecure containers | non-root user, ports bound to `localhost`, no production credentials in the repo |
| Broken API contract unnoticed | CI drift check on the generated client |

## 10. Evaluation criteria (definition of done)

1. `docker compose up` starts the four services in a healthy state.
2. `/health/ready` returns 200, and 503 when PostgreSQL or Redis is stopped.
3. The frontend page reflects this state through the generated client.
4. CI is green on a test PR, fails on a planted fake secret, and fails on a stale generated client.
5. All documentation deliverables of section 8 exist.

## 11. Dependencies and next step

Depends on nothing. After spec approval, write the implementation plan for this sub-project; no code or dependency is added before the plan is approved. Sub-project 1 (Ingestion) follows.
