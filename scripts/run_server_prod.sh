#!/usr/bin/env bash
# Prod-like local server: N workers, no reload, warning-level logs.
# Use before loadtest/locustfile_v2.py to match real deploy topology.
set -eu

PORT="${PORT:-8000}"
WORKERS="${WORKERS:-4}"

echo "→ uvicorn app.main:app  workers=$WORKERS  port=$PORT"
exec uvicorn app.main:app \
  --host 0.0.0.0 --port "$PORT" \
  --workers "$WORKERS" \
  --log-level warning \
  --no-access-log
