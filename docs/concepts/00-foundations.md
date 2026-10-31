# Foundations: concepts and interview questions

Concept notes for sub-project 0. Each concept is explained plainly, then tied to the files where it lives. The questions at the end are practice for an English interview; the answers are short on purpose.

## 1. Twelve-factor configuration and fail-fast

**Idea.** The twelve-factor method says configuration that changes between environments (database URLs, credentials, timeouts) belongs in environment variables, not in code or in files baked into the image. The same build then runs unchanged in development, CI and production. Fail-fast means the program checks its configuration at startup and refuses to run if it is wrong, instead of failing later in the middle of a request.

**In this repo.**
- `backend/src/explorai_backend/config.py` defines `Settings` with `pydantic-settings`. `APP_ENV` must be one of `development`, `test`, `production`; `DATABASE_URL` must be a valid PostgreSQL DSN; `REDIS_URL` a valid Redis DSN; `CHECK_TIMEOUT_SECONDS` is a float with a default of 2.0. A missing or malformed value raises a validation error when settings are first built.
- There is no `.env` loading in the app. Real values come from the process environment. For Docker Compose, `infra/.env` (git-ignored) is passed with `--env-file`, and `.env.example` is the versioned template with placeholders only.
- In Compose, `infra/docker-compose.yml` overrides `DATABASE_URL` and `REDIS_URL` for the backend container so it reaches the databases by service name (`postgres`, `redis`), while the values in `infra/.env` use `127.0.0.1` for tests run on the host.

## 2. Liveness vs readiness

**Idea.** Liveness answers "is the process running?"; if not, an orchestrator restarts it. Readiness answers "can it serve traffic right now?"; if not, the orchestrator stops sending requests but does not restart it. Mixing them is a classic mistake: if liveness checked the database, a database outage would make the orchestrator restart healthy application containers in a loop.

**In this repo.**
- `backend/src/explorai_backend/api/health.py`: `GET /health/live` always returns 200 while the process answers. `GET /health/ready` runs the PostgreSQL and Redis checks and returns 200, or 503 with a per-dependency body such as `{"status": "unavailable", "checks": {"postgres": "down", "redis": "ok"}}`.
- The backend Dockerfile healthcheck calls `/health/live`. The Compose services use their own checks (`pg_isready`, `redis-cli ping`, and a Node fetch for the frontend), and `depends_on` with `condition: service_healthy` makes the backend wait for the databases.
- The frontend (`useBackendHealth.ts`) calls `/health/ready` and shows the dependency statuses, including from the 503 body.

## 3. Dependency injection for testability

**Idea.** A function that receives its collaborators instead of creating them can be tested with fakes. FastAPI's `Depends` is a dependency injection mechanism, and `app.dependency_overrides` replaces a dependency in tests without touching the code under test.

**In this repo.**
- `get_checks` in `health.py` builds the dictionary of check functions (`check_postgres`, `check_redis`) from the settings. The `ready` route receives it with `Depends(get_checks)`.
- `backend/tests/unit/test_health.py` overrides `get_settings` and `get_checks` with fake async functions (always true, always false, hanging, raising). This tests the 200/503 logic, the timeout and the "no secret in the response" rule without any database.
- The real probes in `db.py` are covered separately by integration tests.

## 4. The test pyramid

**Idea.** Many fast, cheap tests at the bottom (unit), fewer slower ones that use real components (integration), and very few full-system tests at the top. Each level catches a different class of bug, and the cost of running and debugging rises as you go up.

**In this repo.**
- **Unit** (`backend/tests/unit`, `frontend/src/**/*.test.tsx`): no services. The backend uses fakes through dependency overrides; the frontend uses Vitest, Testing Library and msw to simulate API responses. They run in seconds and are the default feedback loop.
- **Integration** (`backend/tests/integration`, marked `integration`): the probes and routes against real PostgreSQL and Redis. They exist because fakes cannot prove that the connection code works with real servers. They fail, rather than skip, when `DATABASE_URL` or `REDIS_URL` is missing, so a misconfigured CI cannot pass silently.
- **Smoke** (`infra/smoke.sh`): brings up the whole Compose stack and checks the backend, the frontend and the proxied route. It exists to catch what no lower level can: broken Dockerfiles, wrong service names, ports, and the proxy.
- A coverage gate of 80% (`backend/pyproject.toml`) is a guardrail against untested code, not a target to maximise.

## 5. The API contract

**Idea.** A contract is a machine-readable description of the API that both sides agree on. Generating the client code from it means that a change in the backend that breaks the frontend shows up as a compile or CI failure, not as a runtime surprise.

**In this repo.**
- `backend/scripts/export_openapi.py` writes `backend/openapi.json` (sorted keys, 2-space indent, LF) from the FastAPI app.
- `pnpm --dir frontend gen:api` runs `openapi-typescript` to produce `frontend/src/api/schema.d.ts`; `frontend/src/api/client.ts` uses it through `openapi-fetch`. Both generated files are committed.
- A unit test (`test_openapi_contract.py`) fails if the committed `openapi.json` differs from the app. The CI `contract` job regenerates both files and runs `git diff --exit-code`, which fails on any drift.
- `.gitattributes` forces LF on the generated files so that Windows line endings do not create false drift.
- `client.ts` uses `baseUrl: window.location.origin`: it assumes the frontend and the API are served from the same origin (the Vite dev proxy now, a reverse proxy later).

## 6. Non-root containers and loopback-only ports

**Idea.** A process inside a container that runs as root has more power if it is compromised (and a container escape is more damaging). Running as an unprivileged user limits the damage. Likewise, a published port bound to `0.0.0.0` is reachable from the network; bound to `127.0.0.1` it is reachable only from the machine itself.

**In this repo.**
- `backend/Dockerfile` creates a system user `app` (uid 10001) and switches to it with `USER app`. It is a multi-stage build: the builder installs dependencies, and the runtime image only copies the virtual environment.
- `frontend/Dockerfile` runs as the `node` user.
- `infra/docker-compose.yml` publishes every port as `127.0.0.1:<port>:<port>`. Inside a container the servers bind `0.0.0.0` (needed for Docker's port forwarding to reach them), but from outside they are exposed on loopback only. The Vite dev server defaults to `127.0.0.1` in `vite.config.ts` and the Docker image overrides it with `--host 0.0.0.0`.
- The frontend image is a development image; a production image belongs to a later sub-project.

## 7. Reproducible builds and supply-chain hygiene

**Idea.** The same commit should build the same artifact tomorrow. Floating versions (`latest`, `^1.2`) and mutable references (a Git tag) let a new upstream release, or a compromised one, change your build without any change in your repository. The supply chain is every piece of third-party code, image and automation you run.

**In this repo.**
- Lockfiles: `uv.lock` and `frontend/pnpm-lock.yaml`. CI and Docker install with `uv sync --frozen` and `pnpm install --frozen-lockfile`, which fail instead of re-resolving.
- Pinned base images and tools: `python:3.12.16-slim`, `ghcr.io/astral-sh/uv:0.13.6`, `node:22.23.4-slim`, `pnpm@12.16.1`.
- GitHub Actions are pinned to full commit SHAs (with the version in a comment), because tags can be moved. `permissions: contents: read` and `persist-credentials: false` limit what a compromised step can do.
- Scanning: `gitleaks` for secrets (staged changes in the hook, the pull request's commits in CI, with the binary downloaded and checked against its published SHA256), `pip-audit` on an export of `uv.lock`, `pnpm audit --audit-level=high`. Dependabot opens weekly update PRs for uv, npm, GitHub Actions and Docker.
- TypeScript is pinned to `^5` in `frontend/package.json` because TypeScript 7 breaks `openapi-typescript`. An unpinned toolchain would have broken the contract generation.

## 8. Hooks are convenience, CI is the gate

**Idea.** A local pre-commit hook gives fast feedback, but it runs on the developer's machine and can be skipped (`git commit --no-verify`) or never installed. Only a check that runs on the server and is required for merging can enforce a rule.

**In this repo.**
- `.pre-commit-config.yaml` runs ruff, gitleaks, prettier, eslint and a Conventional Commits check. `mypy` is left out of the hooks because it is too slow per commit.
- `.github/workflows/ci.yml` re-runs the checks on every pull request (and adds `mypy`, tests, the contract check, the smoke test and the audits). Making those jobs required is done through branch protection, which the maintainer configures separately on GitHub.

## 9. Timeouts: never hang on a dependency

**Idea.** A health endpoint that waits forever on a dead database is worse than useless: the orchestrator's probe times out and the failure is reported with no information. Every call to something outside the process needs a bounded time.

**In this repo.**
- In `health.py`, `_run_check` wraps each check in `asyncio.wait_for(check(), timeout)` with `CHECK_TIMEOUT_SECONDS` (default 2.0). A timeout, or any exception, becomes "down". The checks run concurrently with `asyncio.gather`, so the slowest one, not the sum, bounds the response.
- In `db.py`, `check_postgres` also passes `connect_timeout=5` to the driver. That bound only applies to the standalone probe; on the route the shorter `wait_for` timeout wins.
- Failures are logged without the exception text, because connection errors can contain the DSN with its password. A unit test asserts that a secret in an exception does not appear in the response or the logs.
- `test_ready_hanging_check_returns_503_within_timeout` proves a hanging check yields a 503 quickly.

## 10. Two lessons from building this on Windows

**`localhost` resolves to `::1` first.** On Windows, `localhost` can resolve to the IPv6 address `::1` before `127.0.0.1`. The published ports listen on IPv4 only, so the first attempt is refused (or waits) and only then does the client fall back. In this project that stalled PostgreSQL's libpq probe by about 5 seconds and Redis's by about 2 seconds, enough to blow through a health-check timeout. The fix is to use `127.0.0.1` everywhere (`.env.example`, `vite.config.ts`, the smoke test, the documentation).

**psycopg async needs the selector event loop.** Python's default asyncio loop on Windows is `ProactorEventLoop`, which psycopg's async mode does not support. `backend/tests/conftest.py` sets `WindowsSelectorEventLoopPolicy` on Windows. Linux containers use the selector loop already, so the production path is unaffected; running uvicorn natively on Windows would need the same workaround.

Both are examples of why CI and Docker run on Linux while the author develops on Windows: the differences show up as real bugs, and the Linux run is the reference.

## Interview questions and model answers

**1. What does twelve-factor configuration mean, and how do you apply it?**
Configuration that varies by environment lives in environment variables, not in code, so the same image runs everywhere. In Explorai, `Settings` in `config.py` reads `DATABASE_URL`, `REDIS_URL` and the others from the process environment, and `.env.example` documents them with placeholders. The real `.env` is git-ignored. This keeps secrets out of the repository and makes the build identical across environments.

**2. Why validate configuration at startup instead of when it is first used?**
A bad value discovered at the first request fails in production, at a random time, and sometimes only on a rarely used code path. Validating at startup makes the process refuse to start, so the orchestrator never routes traffic to it. Pydantic gives typed fields (`Literal` for `APP_ENV`, `PostgresDsn`, `RedisDsn`), so errors are precise. The cost is that a typo prevents startup, which is exactly the point.

**3. What is the difference between liveness and readiness, and why two endpoints?**
Liveness says the process is running and should not be killed; readiness says it can serve traffic now. The orchestrator restarts a non-live container but only stops routing to a non-ready one. If one endpoint checked the database, a database outage would cause restarts of healthy containers. So `/health/live` always returns 200 and `/health/ready` checks PostgreSQL and Redis, returning 503 when one is down.

**4. Why does `/health/ready` return 503 rather than 200 with a "down" body?**
Load balancers and orchestrators decide from the status code, not the body. A 200 means "send me traffic". 503 Service Unavailable is the standard signal for "temporarily cannot serve". We still return the JSON body with per-dependency status so a human or the frontend can see which dependency is down.

**5. How does dependency injection make this code testable? What would you do without it?**
The `ready` route gets its checks through `Depends(get_checks)`, so tests replace them with `app.dependency_overrides` and fake async functions. That lets unit tests cover the 200, 503, timeout and exception cases in milliseconds with no database. Without injection I would have to patch module-level functions with `unittest.mock`, which couples tests to import paths, or always run against real services, which is slower and less deterministic.

**6. Why have unit, integration and smoke tests? Wouldn't integration tests alone be enough?**
Each level catches different bugs at a different cost. Unit tests with fakes are fast and pinpoint logic errors. Integration tests prove the probes really work with PostgreSQL and Redis, which fakes cannot. The smoke test proves the containers, networking, ports and proxy work together. Integration tests alone would be slower to run and harder to debug, and they would not exercise the Docker images.

**7. How do you keep a TypeScript frontend and a Python backend in sync?**
Through the OpenAPI contract. The backend exports `openapi.json`; `openapi-typescript` generates types and `openapi-fetch` gives a typed client. Both generated files are committed, a backend unit test checks the JSON matches the app, and the CI `contract` job regenerates everything and fails on `git diff`. A route change that is not reflected in the client cannot be merged.

**8. Why not write the TypeScript types by hand, or use a shared schema language such as GraphQL or gRPC?**
Hand-written types drift silently, and the drift only appears at runtime. GraphQL or gRPC would solve the contract problem but add a new protocol, tooling and learning cost, with no requirement for them here. FastAPI already produces OpenAPI for free, so generating the client is the smallest solution that removes the drift risk.

**9. Why run containers as a non-root user, and why bind ports to 127.0.0.1?**
If an attacker compromises the application, a non-root user has far fewer privileges inside the container and a container escape is harder. The backend image creates a dedicated `app` user with a fixed uid. Binding published ports to `127.0.0.1` means only the local machine can reach the databases and dev servers, not the whole network. Inside the container the server must still listen on `0.0.0.0` for Docker's forwarding to work.

**10. What does "reproducible build" mean here, and what do you do to get it?**
The same commit should produce the same result at any time. I commit lockfiles (`uv.lock`, `pnpm-lock.yaml`) and install with `--frozen` so the install fails instead of re-resolving. Base images are pinned to exact versions, and GitHub Actions are pinned to commit SHAs. Dependabot then proposes updates as visible PRs, so versions change deliberately and not by accident.

**11. Why pin GitHub Actions to a commit SHA instead of a version tag?**
A tag such as `v4` is a movable pointer: the action's owner, or an attacker who compromises the account, can repoint it to malicious code that runs with my CI's permissions. A full commit SHA is immutable. I keep the human-readable version in a comment and let Dependabot update both. It is a supply-chain defence at almost no cost.

**12. If you have pre-commit hooks, why also run the same checks in CI?**
Hooks run on the developer's machine, which can skip them with `--no-verify` or never install them. CI runs on the server for every pull request, and with branch protection its jobs can be required before merging. So hooks give fast feedback, and CI is the actual gate. CI also runs slower checks that are not hooks, such as `mypy`, the integration tests and the smoke test.

**13. How do you stop a health check from hanging when a dependency is down?**
Each check is wrapped in `asyncio.wait_for` with a configurable timeout (2 seconds by default), and any timeout or exception is reported as "down". The checks run concurrently with `asyncio.gather`, so the response time is bounded by the slowest one. The driver also has its own `connect_timeout`, but the route-level timeout is the one that actually bounds the endpoint. A test with a deliberately hanging check proves the 503 arrives quickly.

**14. Why not log the exception when a health check fails?**
Connection exceptions often include the DSN, which contains the database password. Logging them would leak a secret into log storage. The code logs only the check name and "failed" or "timed out", and a unit test makes a check raise a message containing a fake secret and asserts it appears neither in the response nor in the logs.

**15. What went wrong on Windows, and what did it teach you?**
Two things. First, `localhost` resolved to the IPv6 `::1` before IPv4, and since the ports listen on IPv4 each probe stalled before falling back (about 5 seconds for PostgreSQL, about 2 for Redis), so I use `127.0.0.1` everywhere. Second, psycopg's async mode does not work on Windows' default Proactor event loop, so the tests select the selector loop. The lesson is to develop against the same OS as production when possible, and to treat the Linux CI run as the reference.

**16. Why a monorepo with independent services instead of separate repositories?**
One developer and a tightly coupled API contract: a change to the backend and its client should be one reviewed, tested pull request, and the `contract` CI job needs both sides in one place. Independence is kept by giving each service its own dependencies, lockfile, Dockerfile and tests, and letting them talk only over HTTP. The cost is a growing CI and the need for discipline about boundaries.
