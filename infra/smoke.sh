#!/usr/bin/env bash
# End-to-end smoke test: brings the whole Compose stack up, asserts it works, tears it down.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Dedicated project name: `down -v` below can never touch a developer's own stack.
compose() { docker compose -p explorai-smoke --env-file infra/.env -f infra/docker-compose.yml "$@"; }

if [ ! -f infra/.env ]; then
  echo "infra/.env missing: copying from .env.example"
  cp .env.example infra/.env
fi

# Capture the exit status first so the diagnostics and teardown cannot mask it.
cleanup() {
  local status=$?
  if [ "$status" -ne 0 ]; then
    echo "Smoke test failed (exit $status); container logs follow." >&2
    compose logs --no-color --tail=100 >&2 || true
  fi
  echo "Tearing down..."
  compose down -v || true
  exit "$status"
}
trap cleanup EXIT

compose up -d --build --wait --wait-timeout 300

check() {
  local name="$1" url="$2" code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$url" || true)"
  if [ "$code" != "200" ]; then
    echo "FAIL: $name ($url returned ${code:-no response}, expected 200)" >&2
    exit 1
  fi
  echo "PASS: $name"
}

check "backend readiness" "http://127.0.0.1:8000/health/ready"
check "frontend serves index" "http://127.0.0.1:5173/"
check "frontend proxies /health/ready to backend" "http://127.0.0.1:5173/health/ready"

# --- ingestion stack -------------------------------------------------------
set -a; . infra/.env; set +a

migrate_exit="$(docker inspect -f '{{.State.ExitCode}}' "$(compose ps -a -q migrate)")"
if [ "$migrate_exit" != "0" ]; then
  echo "FAIL: migrate exited with $migrate_exit, expected 0" >&2
  exit 1
fi
echo "PASS: migrations applied"

table="$(compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "select to_regclass('public.sources')")"
if [ "$table" != "sources" ]; then
  echo "FAIL: table sources missing after migration (got '${table}')" >&2
  exit 1
fi
echo "PASS: schema present"

check "prefect server health" "http://127.0.0.1:4200/api/health"

# The pipelines container is waited on by `up --wait` through its healthcheck.
# A real arXiv batch needs outbound network access, so it is opt-in: CI leaves
# SMOKE_NETWORK unset to stay independent of a third-party API.
if [ "${SMOKE_NETWORK:-0}" = "1" ]; then
  compose exec -T pipelines python -m explorai_pipelines ingest-arxiv --query "cat:cs.CL" --max-results 2
  versions="$(compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "select count(*) from document_versions where status = 'ok'")"
  if [ "${versions:-0}" -lt 1 ]; then
    echo "FAIL: no ingested document version after the arXiv batch" >&2
    exit 1
  fi
  echo "PASS: arXiv batch ingested ($versions versions)"
fi
