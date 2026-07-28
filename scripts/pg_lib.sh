#!/usr/bin/env bash
# Shared plumbing for the backup and restore scripts.
#
# PostgreSQL's client tools refuse to dump a server newer than themselves, and
# the database usually lives in a container anyway, so every tool call goes
# through one indirection: set PG_CONTAINER to run them inside that container,
# leave it empty to use the binaries on PATH.
#
#   PG_CONTAINER=postgres   ./scripts/pg_backup.sh sora_earth
#   PG_CONTAINER=           ./scripts/pg_backup.sh sora_earth

PG_CONTAINER="${PG_CONTAINER:-}"
PGUSER="${PGUSER:-postgres}"

# Milliseconds since the epoch. `date +%s%3N` is a GNU extension and is not
# available on macOS, and bash's SECONDS is whole seconds — too coarse to
# report an RTO for a small database.
now_ms() { python3 -c 'import time; print(int(time.time() * 1000))'; }

# Human-readable elapsed time from a now_ms() reading.
since_ms() { python3 -c "import sys; print(f'{(int(sys.argv[2]) - int(sys.argv[1])) / 1000:.2f}s')" "$1" "$(now_ms)"; }

# Run a postgres client tool, streaming stdout back to the caller.
pg_tool() {
    if [ -n "$PG_CONTAINER" ]; then
        docker exec "$PG_CONTAINER" "$@"
    else
        "$@"
    fi
}

# Same, but with stdin attached — for pg_restore reading a dump from the host.
pg_tool_stdin() {
    if [ -n "$PG_CONTAINER" ]; then
        docker exec -i "$PG_CONTAINER" "$@"
    else
        "$@"
    fi
}

# Emit a deterministic description of a database: what is in it, and what
# shape it has. Two of these compared across a backup/restore cycle is the
# actual proof that nothing was lost. A byte comparison of dump files would
# not work — pg_dump output is not reproducible byte-for-byte.
pg_fingerprint() {
    local db="$1"
    # stdin variant: the SQL below is fed in on stdin, which plain
    # `docker exec` would not forward.
    pg_tool_stdin psql -U "$PGUSER" -d "$db" -tAF'|' -q <<'SQL'
SELECT 'alembic', version_num FROM alembic_version;

SELECT 'table', table_name
  FROM information_schema.tables
 WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
 ORDER BY table_name;

SELECT 'view', table_name
  FROM information_schema.views
 WHERE table_schema = 'public'
 ORDER BY table_name;

SELECT 'column', table_name || '.' || column_name || ' ' || data_type
       || ' null=' || is_nullable || ' default=' || coalesce(column_default, '-')
  FROM information_schema.columns
 WHERE table_schema = 'public'
 ORDER BY table_name, column_name;

SELECT 'constraint', conrelid::regclass || ' ' || conname || ' ' || pg_get_constraintdef(oid)
  FROM pg_constraint
 WHERE connamespace = 'public'::regnamespace
 ORDER BY conrelid::regclass::text, conname;

SELECT 'index', indexname || ' ' || indexdef
  FROM pg_indexes
 WHERE schemaname = 'public'
 ORDER BY indexname;

-- Row counts for every table, without naming them by hand.
SELECT 'rows', table_name || '=' || (xpath('/row/c/text()', x))[1]::text
  FROM (
    SELECT table_name,
           query_to_xml(format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name),
                        false, true, '') AS x
      FROM information_schema.tables
     WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
  ) counted
 ORDER BY table_name;

-- Content hash of the table the convergence migration will later rewrite.
SELECT 'data:region_esg_scores', md5(string_agg(t::text, E'\n' ORDER BY t.region_code))
  FROM region_esg_scores t;
SQL
}
