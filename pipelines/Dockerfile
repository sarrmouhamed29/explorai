# syntax=docker/dockerfile:1
# Build context is the repository root: docker build -f pipelines/Dockerfile .
# One image serves three Compose services: migrate, prefect (server) and pipelines.

FROM python:3.12.16-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY backend/ backend/
COPY db/ db/
COPY ai/ ai/
COPY pipelines/ pipelines/
RUN uv sync --frozen --package explorai-pipelines --no-dev --no-editable

FROM python:3.12.16-slim AS runtime
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home app \
    && mkdir -p /data/blobs /data/prefect \
    && chown -R app:app /data
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PREFECT_HOME=/data/prefect BLOB_DIR=/data/blobs
USER app
CMD ["python", "-m", "explorai_pipelines", "serve"]
