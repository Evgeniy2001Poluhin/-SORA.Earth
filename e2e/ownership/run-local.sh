#!/usr/bin/env bash
set -euo pipefail

# Run e2e ownership tests locally with a disposable PostgreSQL container.
#
# Creates a uniquely-named container, runs migrations, bootstraps users,
# builds the SPA, starts the backend, runs Playwright tests, and cleans up.
#
# Usage:
#   cd e2e/ownership
#   bash run-local.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Use .venv311 from the main repository (memory: "Local Python cannot run most of this suite")
MAIN_REPO="/Users/evgenijpoluhin/sora_earth_ai_platform"
VENV="$MAIN_REPO/.venv311"

if [ ! -d "$VENV" ]; then
  echo "ERROR: .venv311 not found at $VENV"
  echo "This script requires the Python virtual environment from the main repository."
  exit 1
fi

# Activate venv
source "$VENV/bin/activate"

# Unique container name based on timestamp
CONTAINER_NAME="sora-e2e-ownership-$$"
PG_PORT=15432  # Non-default port to avoid conflicts
BACKEND_PORT=18765  # Dedicated port for e2e tests (not 8000 - dev stack owns that)
PG_PASSWORD="test2026"
DATABASE_URL="postgresql://sora:${PG_PASSWORD}@127.0.0.1:${PG_PORT}/sora_earth"

UVICORN_PID=""

cleanup() {
  echo "=== Cleanup ==="

  # Stop backend if running
  if [ -n "$UVICORN_PID" ]; then
    echo "Stopping uvicorn (PID $UVICORN_PID)..."
    kill "$UVICORN_PID" 2>/dev/null || true
    wait "$UVICORN_PID" 2>/dev/null || true
  fi

  # Remove PostgreSQL container
  echo "Removing PostgreSQL container..."
  docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

  echo "Cleanup complete."
}

trap cleanup EXIT

echo "=== Starting disposable PostgreSQL ==="
docker run -d \
  --name "$CONTAINER_NAME" \
  -e POSTGRES_USER=sora \
  -e POSTGRES_PASSWORD="$PG_PASSWORD" \
  -e POSTGRES_DB=sora_earth \
  -p "${PG_PORT}:5432" \
  postgres:16

echo "Waiting for PostgreSQL to be ready..."
sleep 3  # Give PostgreSQL a moment to start
for i in {1..60}; do
  if docker exec "$CONTAINER_NAME" pg_isready -U sora >/dev/null 2>&1; then
    echo "PostgreSQL is ready (pg_isready)"
    # Wait for it to actually accept connections (not just pg_isready)
    for j in {1..10}; do
      if docker exec "$CONTAINER_NAME" psql -U sora -d sora_earth -c "SELECT 1" >/dev/null 2>&1; then
        echo "PostgreSQL is accepting connections"
        break
      fi
      sleep 1
    done
    break
  fi
  if [ "$i" -eq 60 ]; then
    echo "PostgreSQL failed to become ready within 60 seconds"
    exit 1
  fi
  sleep 1
done

echo "=== Running migrations ==="
cd "$PROJECT_ROOT"
DATABASE_URL="$DATABASE_URL" python3 -m alembic upgrade head

echo "=== Bootstrapping development accounts ==="
DATABASE_URL="$DATABASE_URL" python3 scripts/bootstrap_users.py --dev

echo "=== Building SPA in production mode ==="
cd "$PROJECT_ROOT/web"
npm ci --engine-strict=false
# Build with explicit API base and mocks OFF (otherwise falls into demo/mock mode)
VITE_API_BASE=/api/v1 VITE_USE_MOCK=false npm run build

echo "=== Installing Playwright dependencies ==="
cd "$SCRIPT_DIR"
npm ci --engine-strict=false
npx playwright install chromium --with-deps

echo "=== Starting backend with SPA ==="
# Verify port is free (lsof + grep can return 1, don't let that exit script)
if lsof -nP -iTCP:"$BACKEND_PORT" 2>/dev/null | grep -q LISTEN || false; then
  echo "ERROR: Port $BACKEND_PORT is already in use:"
  lsof -nP -iTCP:"$BACKEND_PORT" || true
  exit 1
fi

# Unset REDIS_URL to avoid Redis connection (app handles missing Redis gracefully)
unset REDIS_URL

# Use explicit log file (not $$ which might be tricky with redirects)
UVICORN_LOG="/tmp/e2e-uvicorn-$CONTAINER_NAME.log"

cd "$PROJECT_ROOT"
DATABASE_URL="$DATABASE_URL" \
  SECRET_KEY="local-test-secret" \
  SORA_ADMIN_TOKEN="local-test-admin" \
  SORA_OFFLINE="1" \
  RUN_SCHEDULER="false" \
  "$VENV/bin/uvicorn" app.main:app \
    --host 127.0.0.1 \
    --port "$BACKEND_PORT" \
    >> "$UVICORN_LOG" 2>&1 &

UVICORN_PID=$!
echo "Backend started (PID $UVICORN_PID, port $BACKEND_PORT, log: $UVICORN_LOG)"

echo "Waiting for backend to be ready..."
READY=false
# Importing app.main loads torch, SHAP and the models; on a busy Mac that alone
# exceeded 60 s (load average 14-20 while another job ran). Bounded, not open-ended.
READY_SECONDS="${E2E_READY_SECONDS:-240}"
for i in $(seq 1 "$READY_SECONDS"); do
  # All probe commands must not fail the script (set -e + pipefail)
  if kill -0 "$UVICORN_PID" 2>/dev/null; then
    # lsof returns 1 when nothing matches - don't let that exit the script
    # Exit status, not a line count: "wc -l || echo 0" produced "0\n0" when lsof
    # found nothing. lsof exits 0 only if OUR pid listens on OUR port.
    if lsof -nP -iTCP:"$BACKEND_PORT" -sTCP:LISTEN -a -p "$UVICORN_PID" >/dev/null 2>&1; then
      # curl -sf returns nonzero on failure - wrap in if to avoid script exit
      if curl -sf "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null 2>&1; then
        # Check SPA is served - grep must actually match (not masked with || true)
        SPA_RESPONSE=$(curl -sf "http://127.0.0.1:$BACKEND_PORT/" 2>/dev/null || echo "")
        if echo "$SPA_RESPONSE" | grep -iq "<!doctype html"; then
          echo "Backend is ready and serving SPA from worktree"
          READY=true
          break
        fi
      fi
    fi
  fi
  sleep 1
done

if [ "$READY" = "false" ]; then
  echo "Backend failed to become ready within ${READY_SECONDS} seconds"
  echo "Last 50 lines of uvicorn log ($UVICORN_LOG):"
  tail -50 "$UVICORN_LOG" 2>/dev/null || echo "(log file not created)"
  exit 1
fi

echo "=== Running Playwright tests ==="
cd "$SCRIPT_DIR"
BASE_URL="http://127.0.0.1:$BACKEND_PORT" npx playwright test --reporter=list,json

echo "=== Generating report artifact ==="
npx ts-node generate-report.ts

echo "=== Test run complete ==="
if [ -f e2e-ownership-report.json ]; then
  cat e2e-ownership-report.json
fi
