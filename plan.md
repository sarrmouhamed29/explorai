# Master Plan: Explorai

An AI-powered research and decision intelligence platform: document upload, a RAG assistant with citations, research agents, analytics/ML and automated reports. This document is the global vision. Each sub-project gets its own detailed spec (`docs/superpowers/specs/`), then an implementation plan.

## Goal and assumptions

- **Goal (assumed, to confirm):** learn AI engineering end to end and build a portfolio project. The author must understand and be able to justify every choice in an English job interview. No known cloud budget constraint.
- **Source of ideas:** `description.md`, an initial ideation, non-binding. This plan is authoritative. The project must demonstrate an AI engineer's skills: data, ML, LLM/RAG, agents, evaluation, AI safety, MLOps/LLMOps, cloud/DevOps.

## Working principles

- **English first:** code, comments, commits, PRs, README, ADRs and specs are in English. Conversation with the assistant stays in French.
- **Understand before building:** every technical choice is explained with the alternatives considered and the trade-offs. Concepts are explained before they are implemented.
- **Documentation grows with the code:** documentation is part of the definition of done. A change that alters behaviour, architecture or setup updates its docs in the same PR. See "Documentation" below.
- **Every spec contains** an "evaluation criteria" section (how we measure that it works) and a "threats and guardrails" section (what can go wrong and how we protect against it).

## Architecture decisions

| Topic | Decision |
|---|---|
| Repository | One repo, independent services (`backend/`, `frontend/`) with their own dependencies and Dockerfile |
| Backend | Python + FastAPI + Pydantic: API, authentication, RAG, agents, evaluation |
| Frontend | React + TypeScript (Vite), single-page app, no server-side business logic |
| Contract | OpenAPI, TypeScript client generated from the backend |
| Data | PostgreSQL, Redis, vector store (Qdrant or pgvector, decided in the RAG spec) |
| Git | **GitFlow**: `main` (tagged releases) and `develop` (integration); `feature/*`, `release/*`, `hotfix/*` branches. PRs required, green CI required, no direct push to `main` or `develop`. Conventional Commits |
| Layout | `backend/` (thin API), `ai/` (library: `rag/`, `agents/`, `ml/`, `safety/`), `evaluation/`, `pipelines/`, `frontend/`, `infra/`, `docs/`, `notebooks/`, `.github/`, root `uv` workspace |
| Boundaries | `ai/` never depends on `backend/`; `evaluation/` and `pipelines/` reuse `ai/` without going through the API; tests live in each package; each major technical choice is recorded in `docs/adr/` |
| Next.js | Rejected as backend: the AI ecosystem is Python, and a third service would double the security surface |

### Tooling

| Area | Choice | Main alternatives not chosen |
|---|---|---|
| Python packaging | `uv` (lockfile, workspaces) | pip + venv, Poetry, conda |
| Python lint/format | `ruff` | flake8 + black + isort, pylint |
| Python typing | `mypy` strict | pyright |
| Python tests | `pytest` + coverage | unittest |
| Frontend | `pnpm`, ESLint, Prettier, strict TypeScript, Vitest | npm/yarn, Biome, Jest |
| Local hooks | `pre-commit` (lint, format, commit message format, secret detection) | husky, lefthook |
| CI | GitHub Actions: lint, typing, tests, Docker build, dependency and secret scans on every PR | GitLab CI, Jenkins |
| Secrets | `.env` git-ignored, versioned `.env.example`, `gitleaks` | secrets in code (never) |
| Releases | changelog generated from Conventional Commits (`git-cliff`); the tag is created by hand when `release/x.y.z` is merged into `main` | release-please / semantic-release (poor fit with GitFlow release branches) |

Local hooks are a convenience (they can be bypassed with `--no-verify`); the required CI check is the real gate.

## Documentation

- `README.md` at the root and in every package: purpose, setup, how to run and test.
- `docs/adr/`: one Architecture Decision Record per major choice (context, decision, alternatives, consequences).
- `docs/concepts/`: a short English note per sub-project explaining the concepts used, with interview questions and answers.
- `docs/superpowers/specs/` and plans: the detailed spec and implementation plan of each sub-project.
- API documentation generated from OpenAPI; docstrings on public interfaces.
- `CHANGELOG.md` generated from commits at release time.

## Sub-projects

Each entry is one spec → plan → implementation → demo cycle.

### 0. Foundations
- **Deliverable:** monorepo skeleton, Docker Compose (PostgreSQL, Redis), configuration and secrets handling, base CI, hooks, conventions, documentation skeleton.
- **Depends on:** nothing.
- **Done when:** `docker compose up` starts the environment and CI passes on the tested skeleton.

### 1. Ingestion
- **Deliverable:** collection from PDFs, websites and APIs; text extraction; cleaning, normalisation, deduplication; structured storage in PostgreSQL; scheduled runs.
- **Depends on:** 0.
- **Done when:** a batch of heterogeneous documents is ingested reproducibly and idempotently.

### 2. Core backend
- **Deliverable:** FastAPI API, data model, authentication and authorisation, per-user data isolation, OpenAPI contract, API tests.
- **Depends on:** 0, 1.
- **Done when:** a user can never read another user's data (automated test) and the API is documented.

### 3. RAG
- **Deliverable:** chunking, embeddings, semantic and hybrid search, reranking, query rewriting, answers with citations, handling of missing information.
- **Depends on:** 1, 2.
- **Done when:** the assistant answers with verifiable sources and refuses to answer when the corpus lacks the information.

### 4. Evaluation
- **Deliverable:** reference test set, metrics (retrieval accuracy, answer correctness, citation quality, hallucinations, latency, cost), Ragas/DeepEval, Langfuse tracing, automated evaluation in CI.
- **Depends on:** 3.
- **Done when:** every RAG change is compared with a baseline using numbers.

### 5. Safety
- **Deliverable:** prompt injection defence (documents and web pages), PII detection, input and output filters, access control down to the retrieval layer, adversarial tests.
- **Depends on:** 3, 4.
- **Done when:** a versioned adversarial test suite passes in CI and no cross-user leak is possible.

### 6. Frontend
- **Deliverable:** React + TypeScript: login, chat with citations, knowledge base management, dashboard, reports.
- **Depends on:** 2, 3 (stable API contract).
- **Done when:** a user uploads a document and gets a sourced answer from the interface.

### 7. Analytics and ML
- **Deliverable:** feature engineering, classification and regression models compared with a baseline, experiment and version tracking (MLflow), prediction API, visualisations.
- **Depends on:** 1, 2.
- **Done when:** experiments are reproducible and the model beats the baseline on the chosen metrics.

### 8. Agents
- **Deliverable:** LangGraph research agent with tools (web search, database queries), planning and task decomposition, multi-agent workflow, human approval for sensitive actions, failure recovery and state persistence.
- **Depends on:** 3, 4, 5.
- **Done when:** a measured success rate on multi-step tasks, with tool selection and planning evaluated.

### 9. Automation
- **Deliverable:** scheduled research and reports, report history and delivery.
- **Depends on:** 8.
- **Done when:** a scheduled report is generated unattended with full traceability.

### 10. Production
- **Deliverable:** AWS deployment with Docker and Terraform, full CI/CD, monitoring (Prometheus, Grafana: latency, tokens, cost), hardening.
- **Depends on:** at least 0 to 6.
- **Done when:** the application is deployed reproducibly, monitored, and alerts work.

### 11. Advanced challenges
- **Deliverable:** comparison of chunking/embedding/reranker strategies, fine-tuning an open model, model routing, caching and token optimisation, scalability (async, queues).
- **Depends on:** 10.
- **Done when:** measured gains in latency, cost or quality are documented.

## Order and milestones

1. **Demonstrable core:** 0 → 1 → 2 → 3. A first end-to-end document assistant through the API.
2. **Quality and trust:** 4 → 5. Measurement and safety before adding features.
3. **Usable product:** 6. A complete interface on a stable backend.
4. **Extended intelligence:** 7 → 8 → 9. Sub-project 7 is independent of RAG and can be moved or deferred.
5. **Production:** 10 → 11.

## Open points, to settle in the specs

- Qdrant or pgvector (spec 3).
- LLM and embedding models: hosted API, local (Ollama), or both (spec 3).
- Airflow or a lighter scheduler for ingestion (spec 1).
- LangChain or LlamaIndex for RAG (spec 3).
- Source of the reference test set (spec 4).

## Next step

Write the detailed spec of sub-project 0 (Foundations), then its implementation plan. No code and no dependencies before both documents are approved.

Update: sub-projects 0 (Foundations) and 1 (Ingestion) are implemented; sub-project 2 (Core backend) comes next, starting with its own spec.
