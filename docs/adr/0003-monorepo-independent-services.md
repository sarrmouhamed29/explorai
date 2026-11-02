# 0003. One repository, independent services

- Status: Accepted
- Date: 2026-10-31

## Context

The project has several parts (backend, frontend, and later `ai/`, `evaluation/`, `pipelines/`) that evolve together and are built and tested by one developer. They use different languages and toolchains.

## Decision

Use one Git repository (monorepo). Each service is independent: it has its own dependency manifest and lockfile (Python through the root uv workspace, the frontend through its own `package.json` and `pnpm-lock.yaml`), its own Dockerfile, its own README and its own tests. Services talk to each other through defined interfaces (HTTP and the OpenAPI contract), not by importing each other's code. Packages are added to the workspace only when their sub-project starts.

## Alternatives considered

- **Several repositories (polyrepo).** Clear boundaries and independent release cycles, but a change to the API contract would need coordinated PRs in several repositories; for one developer this is overhead with no benefit.
- **A single package with shared dependencies.** Simple at first, but it couples the Python and Node toolchains, makes the Docker images larger and blurs the boundaries we want to keep.
- **A monorepo build orchestrator** (Nx, Turborepo, Bazel). Powerful at scale, but unnecessary for two services and a lot to learn and justify.

## Consequences

- One PR can change the backend, the contract and the frontend together, and CI can check that they agree (the `contract` job).
- Each service has its own Dockerfile and lockfile. The backend image builds from the repository root because it uses the shared uv lockfile; the frontend image builds from `frontend/`.
- CI cost grows with the repository; path filters or per-service jobs may be needed later.
- Discipline is required: nothing stops one service from reaching into another except review and tests.
