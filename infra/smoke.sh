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

# --- user journey through the frontend proxy ---------------------------------
# Everything below goes through the frontend origin (5173), as the browser does:
# it proves the /api proxy, the Origin check, the session cookie, the upload, the
# asynchronous pipeline (process-pending, every 30 s) and the per-user isolation.
BASE="http://127.0.0.1:5173/api/v1"
ORIGIN="http://127.0.0.1:5173"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"; cleanup' EXIT

run_id="$(date +%s)-$RANDOM"
password="smoke-test-password-$run_id"
# Never echo the password or the cookie jars: only statuses and ids are printed.
api() { # api <jar> <method> <path> [curl args...] -> body in $WORK/body, status on stdout
  local jar="$1" method="$2" path="$3"
  shift 3
  curl -sS -o "$WORK/body" -w '%{http_code}' --max-time 30 -b "$jar" -c "$jar" \
    -H "Origin: $ORIGIN" -X "$method" "$@" "$BASE$path" || true
}
expect() { # expect <name> <got> <want>
  if [ "$2" != "$3" ]; then
    echo "FAIL: $1 (got ${2:-no response}, expected $3)" >&2
    exit 1
  fi
  echo "PASS: $1"
}

for who in alice bob; do
  code="$(api "$WORK/$who.jar" POST /auth/signup -H 'Content-Type: application/json' \
    -d "{\"email\":\"$who-$run_id@example.com\",\"password\":\"$password\"}")"
  expect "signup $who" "$code" "201"
  code="$(api "$WORK/$who.jar" POST /auth/login -H 'Content-Type: application/json' \
    -d "{\"email\":\"$who-$run_id@example.com\",\"password\":\"$password\"}")"
  expect "login $who" "$code" "204"
done

code="$(api "$WORK/alice.jar" GET /auth/me)"
expect "session cookie accepted" "$code" "200"

code="$(api "$WORK/alice.jar" POST "/documents?filename=2610.40627.pdf" \
  -H 'Content-Type: application/pdf' --data-binary @pipelines/tests/fixtures/annotated/2610.40627.pdf)"
expect "upload accepted" "$code" "202"
doc_id="$(sed -n 's/.*"id":"\([^"]*\)".*/\1/p' "$WORK/body")"
if [ -z "$doc_id" ]; then
  echo "FAIL: upload response carries no document id" >&2
  exit 1
fi

status=""
for _ in $(seq 1 90); do
  code="$(api "$WORK/alice.jar" GET "/documents/$doc_id")"
  if [ "$code" = "200" ] && grep -q '"status":"ready"' "$WORK/body"; then
    status="ready"
    break
  fi
  sleep 2
done
if [ "$status" != "ready" ]; then
  echo "FAIL: document $doc_id not ready within 180 s" >&2
  exit 1
fi
echo "PASS: document processed by the pipeline"

code="$(api "$WORK/alice.jar" GET "/documents/$doc_id/sections")"
expect "sections served" "$code" "200"

code="$(api "$WORK/bob.jar" GET "/documents/$doc_id")"
expect "another user gets 404" "$code" "404"
