#!/usr/bin/env bash
# Restore a dump produced by pg_backup.sh into a database created from nothing.
#
#   PG_CONTAINER=postgres ./scripts/pg_restore.sh backups/sora_earth_....dump sora_earth_restored
#
# The target is dropped first if it exists, so what comes back is built purely
# from the dump — restoring on top of a surviving database proves nothing.
set -euo pipefail

cd "$(dirname "$0")/.."
source scripts/pg_lib.sh

DUMP="${1:-}"
TARGET="${2:-}"
[ -n "$DUMP" ] && [ -n "$TARGET" ] || { echo "usage: $0 <dumpfile> <target-database>" >&2; exit 2; }
[ -f "$DUMP" ] || { echo "no such dump: $DUMP" >&2; exit 2; }

if [ -f "$DUMP.sha256" ]; then
    echo "==> checking the dump against its recorded sha256"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum -c "$DUMP.sha256"
    else
        shasum -a 256 -c "$DUMP.sha256"
    fi
fi

echo "==> dropping and recreating $TARGET"
# WITH (FORCE) evicts live sessions; without it a single open connection is
# enough to make the drop fail and quietly invalidate the drill.
pg_tool psql -U "$PGUSER" -d postgres -q \
    -c "DROP DATABASE IF EXISTS $TARGET WITH (FORCE);" \
    -c "CREATE DATABASE $TARGET;"

echo "==> restoring"
started="$(now_ms)"
# --single-transaction as well as --exit-on-error, and for a sharper reason here
# than in backup_restore.sh: this script drops the target *before* restoring, so
# a partial failure leaves nothing to fall back to. A half-populated database is
# worse than an empty one, because it looks usable.
pg_tool_stdin pg_restore -U "$PGUSER" -d "$TARGET" \
    --single-transaction --exit-on-error < "$DUMP"
elapsed="$(since_ms "$started")"

echo "target      : $TARGET"
echo "duration    : $elapsed"
