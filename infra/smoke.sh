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

compose up -d --build --wait

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
