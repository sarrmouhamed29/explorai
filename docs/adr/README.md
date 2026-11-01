# Architecture Decision Records

An ADR records one significant technical choice: the context, the decision, the alternatives that were rejected and why, and the consequences. They are kept so that the reasoning survives after the decision looks obvious.

| ADR | Title |
| --- | --- |
| [0001](0001-gitflow.md) | GitFlow as the branching model |
| [0002](0002-fastapi-over-nextjs-backend.md) | FastAPI as the backend, not Next.js |
| [0003](0003-monorepo-independent-services.md) | One repository, independent services |
| [0004](0004-uv-workspace.md) | uv workspace for Python packaging |
| [0005](0005-ai-library-independent-of-backend.md) | The `ai/` library does not depend on the backend |
| [0006](0006-prefect-for-orchestration.md) | Prefect for orchestration and scheduling |
| [0007](0007-layered-raw-and-normalised-storage.md) | Layered storage: immutable raw artifacts, then versioned text |
| [0008](0008-shared-db-package.md) | A shared `db/` package owns the schema |

## Template

Copy this for a new ADR, number it with the next free four-digit number, and add it to the table above. To change a decision, write a new ADR and mark the old one "Superseded by NNNN" rather than rewriting it.

```markdown
# NNNN. Title

- Status: Proposed | Accepted | Superseded by NNNN
- Date: YYYY-MM-DD

## Context

## Decision

## Alternatives considered

## Consequences
```
