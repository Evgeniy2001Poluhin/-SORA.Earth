#!/bin/sh
set -e

# This container does not migrate. It verifies and refuses (#125).
#
# It used to run `alembic upgrade head`, and both the backend and the scheduler
# share this file, so `docker compose up -d` started two migrators against one
# database at the same moment. Alembic takes no lock spanning that: whichever
# lost the race died, `restart: unless-stopped` brought it back, and by then the
# migration had usually been applied. Five deployments survived it. That is
# evidence about those five deployments and not about the mechanism -- a restart
# loop is not a synchronisation mechanism, it merely converged.
#
# Migrations are now one deployment step, run once, from the new image, against
# PostgreSQL directly rather than through the transaction pooler:
#
#     docker compose run --rm migrate
#
# scripts/deploy_production.sh does that before it recreates anything, and a
# failure there stops the deployment and rolls the containers back instead of
# restarting one until it works.
#
# It does not roll the schema back. A migration that failed partway, or one
# whose statements are not transactional, leaves the schema where it left it --
# what this arrangement buys is that the schema is never changed by two
# processes at once and never changed after the new code is already serving.
#
# The check below is read-only -- one SELECT of alembic_version, no lock, no
# DDL -- so every replica can run it. A container that starts against a database
# the migrations have not reached would otherwise fail later, at request time,
# to a user.
echo "Verifying schema version..."
python3 ./scripts/verify_schema_head.py

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
