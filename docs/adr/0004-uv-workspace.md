# 0004. uv workspace for Python packaging

- Status: Accepted
- Date: 2026-10-31

## Context

The Python side will grow into several packages (`backend/` now; `ai/`, `evaluation/`, `pipelines/` later) that must share one set of locked, reproducible dependencies. Developers and CI must get the same versions, and installs must be fast.

## Decision

Use `uv` with a workspace. The root `pyproject.toml` declares the workspace (`members = ["backend"]`), and a single `uv.lock` at the root pins every dependency. Commands run through `uv run` and `uv sync`; Docker images and CI install with `uv sync --frozen`, which fails if the lockfile is out of date instead of silently re-resolving. Members are added to the workspace only when their sub-project starts.

## Alternatives considered

- **pip + venv with `requirements.txt`.** Universal, but no real lockfile, no workspace concept and slower installs; pinning transitive dependencies is manual.
- **Poetry.** Mature lockfile and packaging, but slower, and its support for several local packages sharing one lock is weaker than uv's workspaces.
- **conda.** Good for native scientific dependencies, but heavier and not needed for the pure-Python dependencies used so far. It can be reconsidered if an ML dependency requires it.

## Consequences

- One lockfile means dependency updates and audits happen in one place (`pip-audit` runs on an export of `uv.lock`; Dependabot has a `uv` entry).
- The tool is young and moves quickly, so the version is pinned (0.13.6 in CI and in the backend Dockerfile).
- Contributors need uv installed; it can also provision the Python version.
- If packages later need conflicting dependency versions, a single shared lock becomes a constraint and this decision would need revisiting.
