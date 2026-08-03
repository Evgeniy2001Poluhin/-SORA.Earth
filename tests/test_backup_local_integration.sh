#!/usr/bin/env bash
# The real script, against a real PostgreSQL, restoring the archive it published.
#
# The first version of this file called `pg_dump` on the host and restored that.
# It proved PostgreSQL can round-trip a dump -- which was never in question --
# and said nothing about scripts/backup_local_daily.sh, whose whole risk lives in
# the parts it did not touch: running pg_dump inside a Compose service, verifying
# the archive there, and copying it out through a pipe. That copy path is exactly
# where the earlier `pg_restore --list /dev/stdin` defect lived.
#
# So this runs the script itself. The stand is a throwaway Compose project with
# its own volume and no published ports; the only data in it is synthetic.
#
# The behavioural suite (tests/test_backup_local_daily.sh) stays stubbed, because
# it covers branches a live database will not produce on demand -- a dump that
# succeeds while validation fails, a truncated copy out. Neither file replaces
# the other.
set -uo pipefail

PASS=0; FAIL=0
ok()    { PASS=$((PASS+1)); printf '  ok    %s\n' "$1"; }
bad()   { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 — expected [$3], got [$2]"; fi; }

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="${SCRIPT_UNDER_TEST:-$REPO/scripts/backup_local_daily.sh}"

# Linux only, like the behavioural suite and for the same reason: the script
# under test uses flock, which macOS does not have. Running it here would fail on
# the platform rather than on the behaviour and prove nothing either way.
if [ "$(uname -s)" != "Linux" ]; then
    echo "SKIP: this test requires Linux (the script uses flock). Run it in CI,"
    echo "      or on a Linux host with docker and docker compose available."
    exit 0
fi

command -v docker >/dev/null 2>&1 || { echo "SKIP: docker is not available"; exit 0; }
docker compose version >/dev/null 2>&1 || { echo "SKIP: docker compose is not available"; exit 0; }

# The Compose project name comes from the directory basename, so a unique
# directory keeps concurrent runs from colliding.
TMPROOT="$(mktemp -d)"
STAND="$TMPROOT/sora-backup-it-$$"
mkdir -p "$STAND"
BACKUPS="$STAND/backups"
DB_NAME="soratest"
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
      POSTGRES_DB: $DB_NAME
    volumes:
      - standdata:/var/lib/postgresql/data
volumes:
  standdata:
YAML

echo "== the stand =="
docker compose -f "$STAND/docker-compose.yml" up -d >/dev/null 2>&1
DC=(docker compose -f "$STAND/docker-compose.yml" exec -T postgres)

# A real deadline, and a failure if it is not met. Letting an unready database
# fall through would leave every assertion below testing nothing while the run
# stayed green.
READY=0
DEADLINE=$(( SECONDS + 90 ))
while [ "$SECONDS" -lt "$DEADLINE" ]; do
    if "${DC[@]}" pg_isready -U "$DB_USER" -d "$DB_NAME" -t 2 >/dev/null 2>&1; then
        READY=1; break
    fi
    sleep 2
done
if [ "$READY" != 1 ]; then
    echo "  FAIL  the stand never became ready within 90s"
    exit 1
fi
ok "the stand is up and accepting connections"

# Control data chosen so a partial restore is visible. A row count alone would
# not notice a column silently emptied, so specific values are compared too.
"${DC[@]}" psql -U "$DB_USER" -d "$DB_NAME" -q <<'SQL' >/dev/null 2>&1
CREATE TABLE readings (
    id          serial PRIMARY KEY,
    region      varchar(10) NOT NULL,
    value       double precision,
    observed_at timestamptz NOT NULL,
    note        text
);
CREATE UNIQUE INDEX ix_readings_region_time ON readings (region, observed_at);
INSERT INTO readings (region, value, observed_at, note)
SELECT 'R' || g, g * 1.5, timestamptz '2026-01-01 00:00:00+00' + (g || ' hours')::interval,
       CASE WHEN g % 7 = 0 THEN NULL ELSE 'note ' || g END
  FROM generate_series(1, 500) g;
CREATE TABLE empty_on_purpose (id int PRIMARY KEY);
SQL

q() { "${DC[@]}" psql -U "$DB_USER" -d "$1" -tAc "$2" 2>/dev/null | tr -d '[:space:]'; }
check "control data is in place" "$(q "$DB_NAME" 'SELECT count(*) FROM readings')" "500"

echo "== the script publishes a verified archive =="
BACKUP_DIR="$BACKUPS" \
LOCK_FILE="$STAND/lock" \
COMPOSE_FILE="$STAND/docker-compose.yml" \
PROJECT_DIR="$STAND" \
DB_NAME="$DB_NAME" \
DB_USER="$DB_USER" \
KEEP_DAYS=7 \
READY_TIMEOUT=60 \
    bash "$SCRIPT" > "$STAND/out" 2>&1
rc=$?
[ "$rc" = 0 ] || sed 's/^/      /' "$STAND/out"
check "the script exited 0"           "$rc" "0"

check "exactly one archive published" "$(find "$BACKUPS" -name '*.dump' 2>/dev/null | wc -l | tr -d ' ')" "1"
check "no .tmp left behind"           "$(find "$BACKUPS" -name '*.tmp' 2>/dev/null | wc -l | tr -d ' ')" "0"

DUMP="$(find "$BACKUPS" -name "${DB_NAME}_*.dump" 2>/dev/null | head -1)"
check "a checksum accompanies it"     "$([ -n "$DUMP" ] && [ -f "$DUMP.sha256" ] && echo yes || echo no)" "yes"

# The checksum the script recorded must describe the file it published.
#
# Written as a verdict rather than a comparison of two substitutions. The
# comparison form passed when no archive existed at all: both sides were the
# empty string, and empty equals empty. A check that reports success precisely
# when there is nothing to check is worse than no check, and this file's first
# run produced exactly that -- twelve failures and one green line that should
# have been the loudest of them.
ACTUAL_SUM="$(sha256sum "$DUMP" 2>/dev/null | awk '{print $1}')"
RECORDED_SUM="$(awk '{print $1}' "$DUMP.sha256" 2>/dev/null)"
check "the recorded checksum matches the archive" \
    "$([ -n "$ACTUAL_SUM" ] && [ "$ACTUAL_SUM" = "$RECORDED_SUM" ] && echo match || echo "no-match-or-no-archive")" \
    "match"

echo "== that archive restores =="
# Back into the stand, because the archive was written by the container's pg_dump
# and a pg_restore of the same version reads it. Restoring on the host would drag
# in whatever client version the runner happens to carry.
CID="$(docker compose -f "$STAND/docker-compose.yml" ps -q postgres)"
docker cp "$DUMP" "$CID:/tmp/published.dump" >/dev/null 2>&1
check "the published archive copied into the stand" "$?" "0"

"${DC[@]}" psql -U "$DB_USER" -d "$DB_NAME" -qc "DROP DATABASE IF EXISTS restored" >/dev/null 2>&1
"${DC[@]}" psql -U "$DB_USER" -d "$DB_NAME" -qc "CREATE DATABASE restored" >/dev/null 2>&1

"${DC[@]}" pg_restore -U "$DB_USER" -d restored --no-owner --no-acl /tmp/published.dump >/dev/null 2>&1
# Checked explicitly. A partial restore can exit non-zero while the tables and
# rows compared below still happen to be present, so the status is evidence the
# comparisons cannot supply.
check "pg_restore exited 0"           "$?" "0"

# Expected values are literals, not the source database queried a second time.
# Comparing two live queries passes when both return nothing -- a dead stand, a
# failed restore, a psql that errored -- which is the same vacuum the checksum
# check fell into above. The seed data is deterministic, so the numbers can be
# named: 500 rows, 71 of them with a null note (every seventh), two tables.
check "same table count" \
    "$(q restored "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")" "2"

check "same row count" \
    "$(q restored 'SELECT count(*) FROM readings')" "500"

# The content checksum has no literal worth pinning -- it would have to be
# updated by hand whenever the seed changes -- so it is asserted as a verdict:
# both sides present, and equal.
RESTORED_MD5="$(q restored "SELECT md5(string_agg(region||coalesce(value::text,'')||observed_at::text||coalesce(note,''), '|' ORDER BY id)) FROM readings")"
SOURCE_MD5="$(q "$DB_NAME" "SELECT md5(string_agg(region||coalesce(value::text,'')||observed_at::text||coalesce(note,''), '|' ORDER BY id)) FROM readings")"
check "same checksum over the contents" \
    "$([ -n "$SOURCE_MD5" ] && [ "$RESTORED_MD5" = "$SOURCE_MD5" ] && echo match || echo "no-match-or-empty")" \
    "match"

check "nulls survived as nulls" \
    "$(q restored 'SELECT count(*) FROM readings WHERE note IS NULL')" "71"

check "the empty table came across empty, not missing" \
    "$(q restored "SELECT count(*) FROM information_schema.tables WHERE table_name='empty_on_purpose'")" "1"

check "the unique index is there" \
    "$(q restored "SELECT count(*) FROM pg_indexes WHERE tablename='readings' AND indexname='ix_readings_region_time'")" "1"

# --no-owner and --no-acl are in the script's invocation for a reason: roles are
# cluster-level and absent from a single-database dump.
check "restored without needing the original owner" \
    "$(q restored "SELECT count(*) FROM readings WHERE region='R1'")" "1"

echo
echo "  passed: $PASS   failed: $FAIL"
[ "$FAIL" -eq 0 ]
