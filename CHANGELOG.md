# Changelog

All notable changes to this project are documented here. The file is generated from Conventional Commits with git-cliff (see CONTRIBUTING.md).

## [Unreleased]

### Features

- **backend:** Add validated environment configuration (748d896)
- **backend:** Add liveness and readiness routes (37296d0)
- **backend:** Export OpenAPI contract (f153386)
- **frontend:** Add health status page using the generated client (43f80e2)

### Documentation

- Add master plan, foundations spec and plan (6252c7b)
- **backend:** Fix integration test instructions and document probe timeout (6c385f8)
- Add contributing guide, ADRs and foundations concepts (8fe5e77)

### Tests

- **backend:** Add integration tests with real Postgres and Redis (052833b)

### Build

- **infra:** Containerize the backend and add it to Compose (8717a7e)
- **infra:** Add frontend to Compose and end-to-end smoke test (48419a8)
- Pin base images and harden the smoke test (0baaf69)

### CI

- Add GitHub Actions workflows and dependency updates (820f5fc)
- Point dependabot uv updates at the workspace root (17d8166)

### Chores

- Enforce LF line endings for shell scripts and Dockerfiles (c4f2858)
- Add linting, typing and pre-commit hooks (9f0da01)

