#!/usr/bin/env bash
# Prove that a backup of this database can actually bring it back.
#
#   PG_CONTAINER=sora-drill-pg DATABASE_URL=postgresql://... \
#       ./scripts/backup_restore_drill.sh sora_drill
#
# The drill destroys the database it is pointed at. Point it at a disposable
# instance — never at production, and never at a database anyone else is using.
#
# What it establishes, in order: a backup can be taken, the database can be
# lost completely, it can be rebuilt from that backup alone, and what comes
# back is indistinguishable from what went in — schema, constraints, indexes,
# row counts, content, and the Alembic revision.
set -euo pipefail

cd "$(dirname "$0")/.."
source scripts/pg_lib.sh

DB="${1:-sora_drill}"
OUTDIR="${OUTDIR:-$(mktemp -d)}"
FAILURES=0

note()  { printf '\n=== %s ===\n' "$*"; }
check() {  # check <label> <expected> <actual>
    if [ "$2" = "$3" ]; then
        printf '  ok    %-34s %s\n' "$1" "$3"
    else
        printf '  FAIL  %-34s expected %s, got %s\n' "$1" "$2" "$3"
        FAILURES=$((FAILURES + 1))
    fi
}

[ -n "$PG_CONTAINER" ] || echo "note: PG_CONTAINER unset, using postgres tools from PATH"

note "1. state before the backup"
before_rev="$(pg_tool_stdin psql -U "$PGUSER" -d "$DB" -tAc 'SELECT version_num FROM alembic_version')"
before_rows="$(pg_tool_stdin psql -U "$PGUSER" -d "$DB" -tAc 'SELECT count(*) FROM region_esg_scores')"
before_view="$(pg_tool_stdin psql -U "$PGUSER" -d "$DB" -tAc 'SELECT count(*) FROM regional_esg_snapshot')"
echo "  alembic revision : $before_rev"
echo "  region_esg_scores: $before_rows rows"
echo "  through the view : $before_view rows"

note "2. backup"
backup_out="$(./scripts/pg_backup.sh "$DB" "$OUTDIR")"
echo "$backup_out"
DUMP="$(echo "$backup_out" | awk '/^dump  *:/ {print $3}')"
BACKUP_SECONDS="$(echo "$backup_out" | awk '/^duration  *:/ {print $3}')"
[ -f "$DUMP" ] || { echo "backup produced no dump file" >&2; exit 1; }

note "3. destroy the database"
pg_tool psql -U "$PGUSER" -d postgres -q -c "DROP DATABASE IF EXISTS $DB WITH (FORCE);"
still_there="$(pg_tool_stdin psql -U "$PGUSER" -d postgres -tAc \
    "SELECT count(*) FROM pg_database WHERE datname = '$DB'")"
check "database is gone" "0" "$still_there"
[ "$still_there" = "0" ] || { echo "refusing to continue: the drop did not take" >&2; exit 1; }

note "4. restore from the dump alone"
restore_out="$(./scripts/pg_restore.sh "$DUMP" "$DB")"
echo "$restore_out"
RESTORE_SECONDS="$(echo "$restore_out" | awk '/^duration  *:/ {print $3}')"

note "5. verify what came back"
after_rev="$(pg_tool_stdin psql -U "$PGUSER" -d "$DB" -tAc 'SELECT version_num FROM alembic_version')"
after_rows="$(pg_tool_stdin psql -U "$PGUSER" -d "$DB" -tAc 'SELECT count(*) FROM region_esg_scores')"
after_view="$(pg_tool_stdin psql -U "$PGUSER" -d "$DB" -tAc 'SELECT count(*) FROM regional_esg_snapshot')"
check "alembic revision" "$before_rev" "$after_rev"
check "region_esg_scores rows" "$before_rows" "$after_rows"
check "rows through the view" "$before_view" "$after_view"

pg_fingerprint "$DB" > "$OUTDIR/after.fingerprint"

# Compared after normalising CHECK constraint definitions, and nothing else.
#
# `pg_get_constraintdef` renders the stored parse tree; pg_dump writes that as
# SQL and the restore re-parses it, giving an equivalent tree that prints
# differently. Measured here on PostgreSQL 16, 2026-09-03: the array cast in
# ck_environmental_observations_temporal_kind_known moved from the constructor
# to each element. Nothing was lost, and a strict comparison fails on every
# correct restore -- which is how a check ends up deleted rather than fixed.
#
# Only CHECK definitions are touched. Columns, indexes, row counts, the data
# hash and the alembic revision are still compared byte for byte, and the raw
# fingerprints are kept beside the normalised ones so the real difference is
# always available. scripts/pg_fingerprint_normalise.py records what the
# canonical form is blind to; tests/test_fingerprint_normalisation.py asserts it.
NORM="${PYTHON:-python3} $(dirname "$0")/pg_fingerprint_normalise.py"
$NORM < "$DUMP.fingerprint"        > "$OUTDIR/before.normalised"
$NORM < "$OUTDIR/after.fingerprint" > "$OUTDIR/after.normalised"

if diff -u "$OUTDIR/before.normalised" "$OUTDIR/after.normalised" > "$OUTDIR/fingerprint.diff"; then
    printf '  ok    %-34s %s\n' "fingerprint identical" \
        "$(wc -l < "$DUMP.fingerprint" | tr -d ' ') lines"
    # Said out loud when it happens, so "identical" is never read as "byte for
    # byte" on a run where it was not.
    if ! diff -q "$DUMP.fingerprint" "$OUTDIR/after.fingerprint" >/dev/null; then
        printf '        %s\n' "(CHECK constraints re-rendered by the restore; see raw fingerprints)"
    fi
else
    printf '  FAIL  %-34s see %s\n' "fingerprint differs" "$OUTDIR/fingerprint.diff"
    head -40 "$OUTDIR/fingerprint.diff"
    FAILURES=$((FAILURES + 1))
fi

note "6. application smoke test against the restored database"
# PYTHON lets the caller point at a virtualenv that has the app's dependencies.
if "${PYTHON:-python3}" scripts/drill_smoke.py; then
    printf '  ok    %-34s\n' "application reads restored data"
else
    printf '  FAIL  %-34s\n' "application smoke test"
    FAILURES=$((FAILURES + 1))
fi

note "result"
echo "  backup duration  : $BACKUP_SECONDS"
echo "  restore duration : $RESTORE_SECONDS"
echo "  dump             : $DUMP"
echo "  artifacts        : $OUTDIR"
if [ "$FAILURES" -eq 0 ]; then
    echo "  DRILL PASSED"
else
    echo "  DRILL FAILED: $FAILURES check(s)"
    exit 1
fi
