# syntax=docker/dockerfile:1
# Build context is the repository root: docker build -f backend/Dockerfile .

FROM python:3.12.16-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY backend/ backend/
COPY db/ db/
COPY pipelines/ pipelines/
RUN uv sync --frozen --package explorai-backend --no-dev --no-editable

FROM python:3.12.16-slim AS runtime
# /data/blobs is created here so a fresh named volume mounted on it inherits uid 10001
# ownership; the pipelines image does the same, so both services can share the volume.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home app \
    && mkdir -p /data/blobs \
    && chown -R app:app /data
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]
CMD ["uvicorn", "explorai_backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
