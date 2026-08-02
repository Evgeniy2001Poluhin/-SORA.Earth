#!/usr/bin/env bash
# The whole path against a real PostgreSQL: data in, dump, restore elsewhere,
# compare.
#
# The behavioural tests stub pg_dump and pg_restore, which is right for the
# branches a live database cannot produce on demand -- a validation that fails
# while the dump succeeds, for instance. What they cannot show is that the file
# the script produces actually restores. Only a real server can, and a backup
# that has never been restored is a hypothesis.
#
# Control values are chosen so a partial restore is visible: a row count alone
# would not notice a column silently emptied, so specific values are compared too.
set -uo pipefail

PASS=0; FAIL=0
ok()    { PASS=$((PASS+1)); printf '  ok    %s\n' "$1"; }
bad()   { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 — expected [$3], got [$2]"; fi; }

: "${PGHOST:=localhost}"
: "${PGPORT:=5432}"
: "${PGUSER:=sora}"
: "${PGPASSWORD:=sora2026}"
export PGHOST PGPORT PGUSER PGPASSWORD

SRC="backup_src_$$"
DST="backup_dst_$$"
WORK="$(mktemp -d)"
cleanup() {
    psql -d postgres -qc "DROP DATABASE IF EXISTS $SRC" >/dev/null 2>&1
    psql -d postgres -qc "DROP DATABASE IF EXISTS $DST" >/dev/null 2>&1
    rm -rf "$WORK"
}
trap cleanup EXIT

command -v pg_dump >/dev/null 2>&1 || { echo "SKIP: no PostgreSQL client tools"; exit 0; }
psql -d postgres -qc 'SELECT 1' >/dev/null 2>&1 || { echo "SKIP: no PostgreSQL at $PGHOST:$PGPORT"; exit 0; }

echo "== a dump of real data restores into a new database =="

psql -d postgres -qc "DROP DATABASE IF EXISTS $SRC" >/dev/null 2>&1
psql -d postgres -qc "CREATE DATABASE $SRC" >/dev/null 2>&1

psql -d "$SRC" -q <<'SQL' >/dev/null 2>&1
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

# The same invocation the script makes, so this exercises the real format and
# flags rather than a convenient approximation.
pg_dump -d "$SRC" --format=custom --no-owner --no-acl --file "$WORK/test.dump" 2>/dev/null
check "the dump was produced"        "$([ -s "$WORK/test.dump" ] && echo yes || echo no)" "yes"

pg_restore --list "$WORK/test.dump" >/dev/null 2>&1
check "pg_restore accepts it"        "$?" "0"

psql -d postgres -qc "CREATE DATABASE $DST" >/dev/null 2>&1
pg_restore -d "$DST" --no-owner --no-acl "$WORK/test.dump" >/dev/null 2>&1

q() { psql -d "$1" -tAc "$2" 2>/dev/null | tr -d '[:space:]'; }

check "same table count" \
    "$(q "$DST" "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")" \
    "$(q "$SRC" "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"

check "same row count" \
    "$(q "$DST" 'SELECT count(*) FROM readings')" \
    "$(q "$SRC" 'SELECT count(*) FROM readings')"

# A row count alone would not notice a column emptied, a type changed or an
# ordering lost, so specific values are compared as well.
check "same checksum over the contents" \
    "$(q "$DST" "SELECT md5(string_agg(region||coalesce(value::text,'')||observed_at::text||coalesce(note,''), '|' ORDER BY id)) FROM readings")" \
    "$(q "$SRC" "SELECT md5(string_agg(region||coalesce(value::text,'')||observed_at::text||coalesce(note,''), '|' ORDER BY id)) FROM readings")"

check "nulls survived as nulls" \
    "$(q "$DST" 'SELECT count(*) FROM readings WHERE note IS NULL')" \
    "$(q "$SRC" 'SELECT count(*) FROM readings WHERE note IS NULL')"

check "the empty table came across empty, not missing" \
    "$(q "$DST" "SELECT count(*) FROM information_schema.tables WHERE table_name='empty_on_purpose'")" "1"

check "the unique index is there" \
    "$(q "$DST" "SELECT count(*) FROM pg_indexes WHERE tablename='readings' AND indexname='ix_readings_region_time'")" "1"

# --no-owner and --no-acl are in the invocation for a reason: roles are
# cluster-level and absent from a single-database dump. This confirms the restore
# does not depend on them.
check "restored without needing the original owner" \
    "$(q "$DST" "SELECT count(*) FROM readings WHERE region='R1'")" "1"

echo
echo "  passed: $PASS   failed: $FAIL"
[ "$FAIL" -eq 0 ]
