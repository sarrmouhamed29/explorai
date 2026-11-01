# 0006. Prefect for orchestration and scheduling

- Status: Accepted
- Date: 2026-10-31

## Context

Ingestion must run on a schedule, retry network failures, keep a history of runs and stay observable. The steps are Python functions. The project is a one-person learning project, so the tool must be cheap to run locally in Docker Compose, and its concepts (flow, task, retry, schedule, idempotence) should transfer to the tools employers use.

## Decision

Use **Prefect 3**. Flows and tasks are plain Python functions with decorators (`@flow`, `@task`). The nightly arXiv ingestion is served by `flow.serve(cron=...)` in a `pipelines` container; a Prefect server (development settings, SQLite on a volume) provides the UI and run history. Retries apply only to network tasks, and only to transient errors. The business logic stays outside Prefect in `explorai_pipelines.ingest`, so flows are thin wiring and the logic is testable without a server.

## Alternatives considered

- **Airflow.** The industry reference and widely listed in job offers. It needs a scheduler, a web server and a metadata database, and its DAG model is heavier than this need. The time spent running it would exceed the time spent on ingestion. The concepts transfer: a Prefect flow is close to an Airflow DAG, a task to a task.
- **APScheduler or cron plus plain functions.** Zero extra services, but retries, run history and a UI would be reimplemented, and the orchestration would stay invisible, which defeats the learning goal.
- **Dagster.** A good fit for data assets, but it brings its own model (assets, jobs) on top of what this project needs, and Prefect's function-first style is closer to the existing code.

## Consequences

- One extra service (the Prefect server) in Compose. To avoid client and server version drift it runs from the same image as the flows.
- Prefect 3.8.x server code calls `Body(min_items=...)`, which FastAPI removed in 0.155. The shared lockfile therefore carries `constraint-dependencies = ["fastapi<0.154"]` in the root `pyproject.toml`, so the backend lags FastAPI until Prefect fixes this. Dependabot bumps must be checked against it.
- The Prefect server runs in development mode with SQLite. Production hardening (PostgreSQL metadata, authentication, a work pool) belongs to sub-project 10.
- Tests use `prefect_test_harness`, which starts a temporary local API; the first test of a module pays about half a minute of startup.
