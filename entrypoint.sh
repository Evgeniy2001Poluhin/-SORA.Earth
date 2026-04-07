#!/bin/sh
set -e
echo "Running migrations..."
alembic upgrade head
echo "Starting server with Gunicorn (4 workers)..."
exec gunicorn app.main:app \
  -k uvicorn.workers.UvicornWorker \
  -w ${WORKERS:-4} \
  -b 0.0.0.0:8000 \
  --timeout 120 \
  --graceful-timeout 30 \
  --access-logfile -
