# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

The repository is pre-implementation. It contains `LICENSE`, `plan.md` (master plan, authoritative), `description.md` (initial ideation only, non-binding) and this file. There is no source code, build system, linter or test suite yet, so there are no build/lint/test commands to document. Update this file once the first sub-project is scaffolded.

The project is an AI-powered research & decision intelligence platform (document ingestion, RAG with citations, research agents, analytics/ML, scheduled reports) meant to demonstrate the full AI engineer skill set. The maintainer converses in French, but all project artifacts (code, comments, docs, commits, PRs, ADRs) are written in English. The maintainer is learning and must be able to justify every choice in interviews: explain choices and alternatives, and explain concepts before implementing them.

## Decisions (see `plan.md` for detail)

- **Git:** GitFlow. `main` holds tagged releases, `develop` is integration. Work happens on `feature/*`, `release/*`, `hotfix/*` branches, merged by PR with a green CI; no direct pushes to `main` or `develop`. Commits follow Conventional Commits. Remote: `sarrmouhamed29/explorai` on GitHub; do not change GitHub settings (branch protection, etc.) without explicit approval.
- **Stack:** Python + FastAPI backend, React + TypeScript (Vite) frontend, linked by an OpenAPI contract. Next.js as backend was rejected (the AI ecosystem is Python).
- **Target layout:** `backend/` (thin API), `ai/` (library: `rag/`, `agents/`, `ml/`, `safety/`), `evaluation/`, `pipelines/`, `frontend/`, `infra/`, `docs/` (specs, plans, `adr/`), `notebooks/`, `.github/`, with a root `uv` workspace.
- **Boundaries:** `ai/` never depends on `backend/`; `evaluation/` and `pipelines/` reuse `ai/` directly, not through the API. Tests live in each package. Significant technical choices are recorded as ADRs in `docs/adr/`.
- **Cross-cutting:** every sub-project spec must include evaluation criteria and threats/guardrails sections. Evaluation and safety are their own sub-projects, not a final phase.

- **Documentation is part of done:** a change that alters behaviour, architecture or setup updates its docs in the same PR (package READMEs, `docs/adr/`, `docs/concepts/` with interview Q&A, specs and plans).
- **Tooling:** `uv`, `ruff`, `mypy` strict, `pytest`; `pnpm`, ESLint, Prettier, strict TypeScript, Vitest; `pre-commit`; GitHub Actions CI as the required gate; `.env` git-ignored with a versioned `.env.example`; changelog via `git-cliff`, release tags created by hand.

## Workflow

The project is set up through the superpowers brainstorming flow. Each of the sub-projects in `plan.md` gets its own spec (`docs/superpowers/specs/`), implementation plan and implementation cycle. Do not scaffold code or install dependencies before the relevant spec and plan are approved.
