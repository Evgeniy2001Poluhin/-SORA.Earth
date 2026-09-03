#!/usr/bin/env bash
# The restore drill itself, against a real PostgreSQL, migrated from nothing.
#
# scripts/backup_restore_drill.sh is what proves a published dump can rebuild
# production's shape. Nothing had ever run it: GAP-007 stood at PARTIAL for six
# weeks because the backup scripts were merged, tested and documented, and
# nothing scheduled them, and this drill had the same shape of gap one layer up
# -- it existed and nobody had watched it pass.
#
# Running it once by hand, 2026-09-03, found that it could not: the seed script
# hardcoded a schema from a server deleted three weeks earlier
# (scripts/drill_seed_prod_shape.py's own header has the story) and the
# fingerprint comparison failed on every restore because PostgreSQL re-renders
# CHECK constraints after a dump/restore cycle. Both are fixed elsewhere in this
# change; this file is what stops either from silently breaking again.
#
# Follows the shape of tests/test_backup_local_integration.sh: a throwaway
# Compose stand, a real deadline for readiness, nothing published, nothing left
# behind.
set -uo pipefail

PASS=0; FAIL=0
ok()    { PASS=$((PASS+1)); printf '  ok    %s\n' "$1"; }
bad()   { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"; }

REPO="$(cd "$(dirname "$0")/.." && pwd)"

if [ "$(uname -s)" != "Linux" ]; then
    echo "SKIP: this test requires Linux. Run it in CI, or on a Linux host with"
    echo "      docker and docker compose available."
    exit 0
fi

command -v docker >/dev/null 2>&1 || { echo "SKIP: docker is not available"; exit 0; }
docker compose version >/dev/null 2>&1 || { echo "SKIP: docker compose is not available"; exit 0; }

TMPROOT="$(mktemp -d)"
STAND="$TMPROOT/sora-drill-it-$$"
mkdir -p "$STAND"
DB_NAME="sora_drill_it"
DB_USER="sora"

cleanup() {
    docker compose -f "$STAND/docker-compose.yml" down -v --remove-orphans >/dev/null 2>&1
    rm -rf "$TMPROOT"
}
trap cleanup EXIT

# No published ports and a named volume that `down -v` removes. Nothing here is
# reachable from outside the stand, and nothing survives it.
cat > "$STAND/docker-compose.yml" <<YAML
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: $DB_USER
      POSTGRES_PASSWORD: standpassword
      POSTGRES_DB: postgres
    volumes:
      - standdata:/var/lib/postgresql/data
volumes:
  standdata:
YAML

echo "== the stand =="
docker compose -f "$STAND/docker-compose.yml" up -d >/dev/null 2>&1
DC=(docker compose -f "$STAND/docker-compose.yml" exec -T postgres)

# A real deadline, and a failure if it is not met -- the same discipline as
# test_backup_local_integration.sh, for the same reason: an unready database
# left to fall through would leave every assertion below green over nothing.
READY=0
DEADLINE=$(( SECONDS + 90 ))
while [ "$SECONDS" -lt "$DEADLINE" ]; do
    if "${DC[@]}" pg_isready -U "$DB_USER" -t 2 >/dev/null 2>&1; then
        READY=1; break
    fi
    sleep 2
done
if [ "$READY" != 1 ]; then
    echo "  FAIL  the stand never became ready within 90s"
    exit 1
fi
ok "the stand is up and accepting connections"

"${DC[@]}" psql -U "$DB_USER" -d postgres -qc "CREATE DATABASE $DB_NAME" >/dev/null 2>&1

# The drill's own dependencies, not the application's. app.database imports only
# sqlalchemy; alembic drives the migration. Neither torch nor the ML stack in
# requirements.txt is reachable from this path, and installing them would turn a
# one-minute job into a ten-minute one for no coverage this test needs.
DEPS_OUT="$STAND/deps.log"
if ! docker compose -f "$STAND/docker-compose.yml" exec -T postgres \
        sh -c 'command -v python3 >/dev/null' >/dev/null 2>&1; then
    docker compose -f "$STAND/docker-compose.yml" exec -T postgres \
        sh -c 'apt-get update -qq && apt-get install -y -qq python3 python3-venv >/dev/null' \
        > "$DEPS_OUT" 2>&1
fi

# A venv inside the postgres container keeps this test to one service, matching
# the stand's own "nothing published, nothing extra" shape.
docker compose -f "$STAND/docker-compose.yml" exec -T postgres \
    sh -c 'python3 -m venv /tmp/drillvenv && /tmp/drillvenv/bin/pip install --quiet "sqlalchemy>=2" "psycopg[binary]" alembic' \
    >> "$DEPS_OUT" 2>&1
check_deps=$?
if [ "$check_deps" != 0 ]; then
    bad "installing the drill's Python dependencies"
    sed 's/^/      /' "$DEPS_OUT"
else
    ok "the drill's Python dependencies are installed"
fi

CID="$(docker compose -f "$STAND/docker-compose.yml" ps -q postgres)"
docker cp "$REPO/." "$CID:/opt/drill" >/dev/null 2>&1

echo "== seeding production's shape =="
# The drill destroys and rebuilds the database it is pointed at; it does not
# create one. scripts/drill_seed_prod_shape.py is the step that does -- it runs
# the migrations and loads synthetic rows -- and skipping it here is exactly the
# mistake this test caught in its own first run: backup_restore_drill.sh against
# an empty database fails at "SELECT version_num FROM alembic_version" before
# touching anything the drill is meant to exercise.
SEED_OUT="$STAND/seed.out"
docker compose -f "$STAND/docker-compose.yml" exec -T \
    -e DATABASE_URL="postgresql+psycopg://$DB_USER:standpassword@localhost:5432/$DB_NAME" \
    -w /opt/drill \
    postgres /tmp/drillvenv/bin/python scripts/drill_seed_prod_shape.py \
    > "$SEED_OUT" 2>&1
seed_rc=$?
sed 's/^/    /' "$SEED_OUT"
if [ "$seed_rc" = 0 ]; then
    ok "drill_seed_prod_shape.py exited 0"
else
    bad "drill_seed_prod_shape.py exited $seed_rc"
fi

echo "== the drill =="
DRILL_OUT="$STAND/drill.out"
docker compose -f "$STAND/docker-compose.yml" exec -T \
    -e DATABASE_URL="postgresql+psycopg://$DB_USER:standpassword@localhost:5432/$DB_NAME" \
    -e PGUSER="$DB_USER" -e PGPASSWORD=standpassword \
    -e PYTHON=/tmp/drillvenv/bin/python \
    -w /opt/drill \
    postgres bash scripts/backup_restore_drill.sh "$DB_NAME" > "$DRILL_OUT" 2>&1
rc=$?

sed 's/^/    /' "$DRILL_OUT"

if [ "$rc" = 0 ]; then
    ok "backup_restore_drill.sh exited 0"
else
    bad "backup_restore_drill.sh exited $rc"
fi

# The result line is checked as text, not inferred from the exit code alone.
# scripts/backup_restore_drill.sh's own header explains why: a script that
# reports "DRILL PASSED" while returning non-zero, or the reverse, is worse than
# either failure alone, because whichever signal is trusted is sometimes wrong.
if grep -q '^  DRILL PASSED$' "$DRILL_OUT"; then
    ok "the drill's own verdict says PASSED"
else
    bad "the drill did not print DRILL PASSED"
fi

if grep -qE '^  FAIL  ' "$DRILL_OUT"; then
    bad "at least one of the drill's internal checks failed (see output above)"
else
    ok "no internal check failed"
fi

# The regression this test exists to catch: a restore that succeeds while the
# fingerprint comparison reports every correct restore as a difference. Asserted
# by name, not only by the absence of FAIL above, because a passing drill for
# the wrong reason -- the fingerprint step silently skipped, say -- would still
# clear both checks above.
if grep -q 'fingerprint identical' "$DRILL_OUT"; then
    ok "the fingerprint comparison ran and matched"
else
    bad "no 'fingerprint identical' line; the comparison did not run or did not match"
fi

echo
echo "  passed: $PASS   failed: $FAIL"
[ "$FAIL" -eq 0 ]
