#!/usr/bin/env bash
set -euo pipefail

# Run the ownership E2E suite locally against a real stack:
# a disposable PostgreSQL 16 container, alembic + bootstrap_users.py --dev,
# the production SPA build served by uvicorn on 127.0.0.1:18765, Playwright.
# Everything it starts is removed on exit. Works on macOS (no `timeout`
# binary is used) and Linux.
#
# Usage (from anywhere):
#   bash e2e/ownership/run-local.sh
#
# Environment:
#   E2E_VENV        Python venv with requirements.txt and pypdf
#                   (default: /Users/evgenijpoluhin/sora_earth_ai_platform/.venv311)
#   E2E_PG_PORT     host port for PostgreSQL (default 15433)
#   E2E_CONTAINER   container name (default sora-e2e-integration)
#   E2E_READY_SECONDS  backend readiness bound (default 240; importing app.main
#                   loads torch, SHAP and the models)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV="${E2E_VENV:-/Users/evgenijpoluhin/sora_earth_ai_platform/.venv311}"
PY="$VENV/bin/python"
CONTAINER_NAME="${E2E_CONTAINER:-sora-e2e-integration}"
PG_PORT="${E2E_PG_PORT:-15433}"
BACKEND_PORT=18765  # never 8000: the development stack owns that
PG_PASSWORD="test2026"
DATABASE_URL="postgresql://sora:${PG_PASSWORD}@127.0.0.1:${PG_PORT}/sora_earth"
READY_SECONDS="${E2E_READY_SECONDS:-240}"
LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/sora-e2e-ownership.XXXXXX")"
UVICORN_LOG="$LOG_DIR/uvicorn.log"
UVICORN_PID=""
CREATED_CONTAINER=false

[ -x "$PY" ] || { echo "ERROR: no Python at $PY (set E2E_VENV)"; exit 1; }

port_in_use() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

cleanup() {
  echo "=== Cleanup ==="
  if [ -n "$UVICORN_PID" ]; then
    kill "$UVICORN_PID" 2>/dev/null || true
    wait "$UVICORN_PID" 2>/dev/null || true
  fi
  if [ "$CREATED_CONTAINER" = true ]; then
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  fi
  echo "uvicorn log kept at $UVICORN_LOG"
}
trap cleanup EXIT

# Not `docker ps | grep -q`: under pipefail grep's early exit can SIGPIPE
# docker, the pipeline then fails, and an existing container reads as absent.
# A failing `docker ps` must stop the run rather than read as "no container".
existing_names=$(docker ps -a --format '{{.Names}}')
if grep -qx "$CONTAINER_NAME" <<<"$existing_names"; then
  echo "ERROR: container $CONTAINER_NAME already exists; remove it or set E2E_CONTAINER"; exit 1
fi
for p in "$PG_PORT" "$BACKEND_PORT"; do
  if port_in_use "$p"; then echo "ERROR: port $p is in use"; lsof -nP -iTCP:"$p" -sTCP:LISTEN || true; exit 1; fi
done

echo "=== PostgreSQL 16 ($CONTAINER_NAME on 127.0.0.1:$PG_PORT) ==="
docker run -d --name "$CONTAINER_NAME" \
  -e POSTGRES_USER=sora -e POSTGRES_PASSWORD="$PG_PASSWORD" -e POSTGRES_DB=sora_earth \
  -p "127.0.0.1:${PG_PORT}:5432" postgres:16 >/dev/null
CREATED_CONTAINER=true
ready=false
for _ in $(seq 1 60); do
  # A query, not pg_isready: the entrypoint restarts the server once after init.
  if docker exec "$CONTAINER_NAME" psql -U sora -d sora_earth -c "SELECT 1" >/dev/null 2>&1; then ready=true; break; fi
  sleep 1
done
[ "$ready" = true ] || { echo "PostgreSQL not ready within 60 s"; exit 1; }
sleep 2
docker exec "$CONTAINER_NAME" psql -U sora -d sora_earth -c "SELECT 1" >/dev/null

echo "=== Migrations and development accounts ==="
cd "$PROJECT_ROOT"
env -u REDIS_URL DATABASE_URL="$DATABASE_URL" "$PY" -m alembic upgrade head > "$LOG_DIR/alembic.log" 2>&1 \
  || { tail -30 "$LOG_DIR/alembic.log"; exit 1; }
env -u REDIS_URL DATABASE_URL="$DATABASE_URL" "$PY" scripts/bootstrap_users.py --dev

echo "=== Production SPA build (VITE_API_BASE=/api/v1, mocks off) ==="
cd "$PROJECT_ROOT/web"
[ -d node_modules ] || npm ci --no-audit --no-fund --engine-strict=false
env -u VITE_USE_MOCK VITE_API_BASE=/api/v1 npm run build > "$LOG_DIR/spa-build.log" 2>&1 \
  || { tail -30 "$LOG_DIR/spa-build.log"; exit 1; }
if grep -rqF --include='*.js' 'mockEvaluate' "$PROJECT_ROOT/app/static/spa/assets/"; then
  echo "ERROR: the SPA bundle is a mock build"; exit 1
fi

echo "=== Playwright ==="
cd "$SCRIPT_DIR"
[ -d node_modules ] || npm ci --no-audit --no-fund
if [ "$(uname -s)" = "Linux" ]; then npx playwright install chromium --with-deps; else npx playwright install chromium; fi

echo "=== Backend on 127.0.0.1:$BACKEND_PORT ==="
cd "$PROJECT_ROOT"
# REDIS_URL must be unset, not empty: startup fails on an empty value.
env -u REDIS_URL \
  DATABASE_URL="$DATABASE_URL" SECRET_KEY="local-test-secret" SORA_ADMIN_TOKEN="local-test-admin" \
  SORA_OFFLINE="1" RUN_SCHEDULER="false" \
  "$VENV/bin/uvicorn" app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" > "$UVICORN_LOG" 2>&1 &
UVICORN_PID=$!

BASE="http://127.0.0.1:$BACKEND_PORT"
ready=false
for _ in $(seq 1 "$READY_SECONDS"); do
  if ! kill -0 "$UVICORN_PID" 2>/dev/null; then echo "uvicorn exited"; tail -50 "$UVICORN_LOG"; exit 1; fi
  if curl -sf "$BASE/health" >/dev/null 2>&1; then ready=true; break; fi
  sleep 1
done
[ "$ready" = true ] || { echo "Backend not ready within ${READY_SECONDS} s"; tail -50 "$UVICORN_LOG"; exit 1; }
# The SPA itself, at every route the suite opens.
for route in / /evaluate /history /login; do
  body="$(curl -sf "$BASE$route" || true)"
  if ! grep -qi '<div id="root"' <<<"$body"; then
    echo "ERROR: $route does not serve the SPA"; exit 1
  fi
done
echo "Backend ready, SPA served"

echo "=== Tests ==="
cd "$SCRIPT_DIR"
status=0
BASE_URL="$BASE" E2E_PYTHON="$PY" npx playwright test || status=$?
node --experimental-strip-types generate-report.ts
exit "$status"
