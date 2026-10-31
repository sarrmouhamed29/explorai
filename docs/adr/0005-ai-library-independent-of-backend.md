# 0005. The `ai/` library does not depend on the backend

- Status: Accepted (planned boundary; `ai/` does not exist yet)
- Date: 2026-10-31

## Context

Later sub-projects add RAG, agents, ML and safety code. Consumers other than the API need that code: the evaluation harness (run in CI against a reference test set) and the ingestion and scheduling pipelines. If the AI logic lived inside the FastAPI application, those consumers would have to import the web layer or go through HTTP.

The `ai/` directory is not created in the Foundations sub-project (YAGNI). This ADR records the boundary now so that later work follows it.

## Decision

The AI code will be a separate Python library, `ai/` (with `rag/`, `agents/`, `ml/` and `safety/`), added to the uv workspace when its sub-project starts. The rule is that `ai/` never imports from `backend/`. The dependency points one way: `backend/` is a thin API layer that calls `ai/`, and `evaluation/` and `pipelines/` reuse `ai/` directly, not through the API. Tests live in each package.

## Alternatives considered

- **AI logic inside the backend package.** Fewer packages at first, but evaluation and pipelines would depend on the web application, and testing the AI code would need the API's configuration and framework.
- **AI logic exposed only as a separate network service** that others call over HTTP. Strong isolation, but it adds a deployment unit, latency and a new security surface with no current need.
- **No enforced boundary**, relying on convention. Cheap, but dependency cycles tend to appear unnoticed. A one-way rule can later be checked automatically (for example with an import-linter style check in CI).

## Consequences

- The AI code can be tested, evaluated and run in batch jobs without starting the web application.
- The backend stays thin: routing, authentication and validation, with logic delegated to `ai/`.
- Some friction: shared types (for example request or result models) must live in `ai/` or another shared place, not in `backend/`.
- This is a plan, not yet code. If the planned layout changes when sub-project 1 or 3 starts, this ADR must be updated or superseded.
