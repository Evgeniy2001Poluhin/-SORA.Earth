#!/usr/bin/env bash
# Take a restorable backup of a PostgreSQL database, plus the evidence needed
# to prove a later restore was faithful.
#
#   PG_CONTAINER=postgres ./scripts/pg_backup.sh sora_earth backups/
#
# Produces three files next to each other:
#   <db>_<utc>.dump         custom-format dump, the thing you restore from
#   <db>_<utc>.dump.sha256  integrity of the dump file itself
#   <db>_<utc>.fingerprint  what the database contained, for comparison later
#
# The dump is written to the host, not left inside the database container: a
# backup that only exists on the machine it is protecting is not a backup.
set -euo pipefail

cd "$(dirname "$0")/.."
source scripts/pg_lib.sh

DB="${1:-}"
OUTDIR="${2:-backups}"
[ -n "$DB" ] || { echo "usage: $0 <database> [outdir]" >&2; exit 2; }

mkdir -p "$OUTDIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DUMP="$OUTDIR/${DB}_${STAMP}.dump"

echo "==> fingerprinting $DB before the dump"
pg_fingerprint "$DB" > "$DUMP.fingerprint"

echo "==> dumping $DB"
started="$(now_ms)"
# -Fc: compressed custom format, restorable selectively and in parallel.
pg_tool pg_dump -U "$PGUSER" -Fc "$DB" > "$DUMP"
elapsed="$(since_ms "$started")"

if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$DUMP" > "$DUMP.sha256"
else
    shasum -a 256 "$DUMP" > "$DUMP.sha256"
fi

size="$(wc -c < "$DUMP" | tr -d ' ')"
echo "dump        : $DUMP"
echo "size        : $size bytes"
echo "duration    : $elapsed"
echo "sha256      : $(cut -d' ' -f1 < "$DUMP.sha256")"
echo "fingerprint : $DUMP.fingerprint ($(wc -l < "$DUMP.fingerprint" | tr -d ' ') lines)"
