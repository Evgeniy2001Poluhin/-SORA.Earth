#!/usr/bin/env bash
# Prod-like local server: N workers, no reload, warning-level logs.
# Use before loadtest/locustfile_v2.py to match real deploy topology.
set -eu

PORT="${PORT:-8000}"
WORKERS="${WORKERS:-4}"

export SORA_REQUEST_LOG="${SORA_REQUEST_LOG:-1}"
export SORA_REQUEST_LOG_DIR="${SORA_REQUEST_LOG_DIR:-output/request_log}"

echo "→ uvicorn app.main:app  workers=$WORKERS  port=$PORT"
exec uvicorn app.main:app \
  --host 0.0.0.0 --port "$PORT" \
  --workers "$WORKERS" \
  --log-level warning \
  --no-access-log
