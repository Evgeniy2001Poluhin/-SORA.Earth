#!/bin/sh
set -e

echo "Running migrations..."
alembic upgrade head

# If docker-compose passed an override command (e.g. "python3 run_scheduler.py"),
# execute it instead of the default gunicorn server.
if [ "$#" -gt 0 ]; then
    echo "Executing override command: $*"
    exec "$@"
fi

echo "Starting server with Gunicorn (${WORKERS:-4} workers)..."
exec gunicorn app.main:app \
    -k uvicorn.workers.UvicornWorker \
    -w ${WORKERS:-4} \
    -b 0.0.0.0:8000 \
    --timeout 120 \
    --graceful-timeout 30 \
    --access-logfile -
