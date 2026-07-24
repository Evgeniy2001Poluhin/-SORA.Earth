#!/usr/bin/env bash
# PostgreSQL round-trip test for GAP-001.
# DESTRUCTIVE: use only with a dedicated, initially empty test database.

set -euo pipefail

error() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

success() {
    printf 'PASS: %s\n' "$*"
}

if [[ -z "${DATABASE_URL:-}" ]]; then
    error "DATABASE_URL is required"
fi

if [[ "${ALLOW_DESTRUCTIVE_TEST_DB:-}" != "1" ]]; then
    error "ALLOW_DESTRUCTIVE_TEST_DB=1 is required"
fi

if [[ "${APP_ENV:-}" != "test" ]]; then
    error "APP_ENV=test is required"
fi

psql_safe() {
    psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 "$@"
}

scalar() {
    psql_safe -tAc "$1" | tr -d '[:space:]'
}

assert_equal() {
    local actual="$1"
    local expected="$2"
    local description="$3"

    if [[ "$actual" != "$expected" ]]; then
        error "$description"
    fi
}

assert_final_schema() {
    local columns region_type score_count updated_at id_spec counters
    local pk_spec unique_id_index view_kind view_count

    columns=$(psql_safe -tAc "
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'region_esg_scores'
        ORDER BY ordinal_position
    " | xargs)
    assert_equal \
        "$columns" \
        "region_code env_score social_score gov_score total_score confidence updated_at id sources_count signals_used" \
        "region_esg_scores column list or order is incorrect"

    region_type=$(scalar "
        SELECT data_type || ':' || is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'region_esg_scores'
          AND column_name = 'region_code'
    ")
    assert_equal "$region_type" "text:NO" \
        "region_code must be text NOT NULL"

    score_count=$(scalar "
        SELECT COUNT(*)
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'region_esg_scores'
          AND column_name IN (
              'env_score',
              'social_score',
              'gov_score',
              'total_score',
              'confidence'
          )
          AND data_type = 'real'
          AND is_nullable = 'YES'
    ")
    assert_equal "$score_count" "5" \
        "score columns must be nullable PostgreSQL REAL"

    updated_at=$(scalar "
        SELECT
            data_type || ':' ||
            is_nullable || ':' ||
            CASE WHEN column_default IS NULL THEN 'NO_DEFAULT'
                 ELSE 'HAS_DEFAULT'
            END
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'region_esg_scores'
          AND column_name = 'updated_at'
    ")
    assert_equal "$updated_at" \
        "timestampwithtimezone:NO:HAS_DEFAULT" \
        "updated_at must be timestamptz NOT NULL with a server default"

    id_spec=$(scalar "
        SELECT
            data_type || ':' ||
            is_nullable || ':' ||
            is_identity || ':' ||
            COALESCE(identity_generation, 'NONE')
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'region_esg_scores'
          AND column_name = 'id'
    ")
    assert_equal "$id_spec" "bigint:NO:YES:ALWAYS" \
        "id must be bigint NOT NULL GENERATED ALWAYS AS IDENTITY"

    counters=$(scalar "
        SELECT COUNT(*)
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'region_esg_scores'
          AND column_name IN ('sources_count', 'signals_used')
          AND data_type = 'integer'
          AND is_nullable = 'YES'
    ")
    assert_equal "$counters" "2" \
        "sources_count and signals_used must be nullable integers"

    pk_spec=$(scalar "
        SELECT string_agg(a.attname, ',' ORDER BY key_columns.ordinality)
        FROM pg_index i
        JOIN pg_class tbl
          ON tbl.oid = i.indrelid
        JOIN pg_namespace ns
          ON ns.oid = tbl.relnamespace
        JOIN LATERAL unnest(i.indkey)
          WITH ORDINALITY AS key_columns(attnum, ordinality)
          ON TRUE
        JOIN pg_attribute a
          ON a.attrelid = tbl.oid
         AND a.attnum = key_columns.attnum
        WHERE ns.nspname = 'public'
          AND tbl.relname = 'region_esg_scores'
          AND i.indisprimary
    ")
    assert_equal "$pk_spec" "region_code" \
        "primary key must contain only region_code"

    unique_id_index=$(scalar "
        SELECT COUNT(*)
        FROM pg_index i
        JOIN pg_class idx
          ON idx.oid = i.indexrelid
        JOIN pg_class tbl
          ON tbl.oid = i.indrelid
        JOIN pg_namespace ns
          ON ns.oid = tbl.relnamespace
        JOIN LATERAL unnest(i.indkey)
          WITH ORDINALITY AS key_columns(attnum, ordinality)
          ON TRUE
        JOIN pg_attribute a
          ON a.attrelid = tbl.oid
         AND a.attnum = key_columns.attnum
        WHERE ns.nspname = 'public'
          AND tbl.relname = 'region_esg_scores'
          AND idx.relname = 'ix_region_esg_scores_id'
          AND i.indisunique
          AND NOT i.indisprimary
          AND i.indnkeyatts = 1
          AND a.attname = 'id'
    ")
    assert_equal "$unique_id_index" "1" \
        "exact unique non-PK index ix_region_esg_scores_id on id is missing"

    view_kind=$(scalar "
        SELECT c.relkind
        FROM pg_class c
        JOIN pg_namespace n
          ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname = 'regional_esg_snapshot'
    ")
    assert_equal "$view_kind" "v" \
        "public.regional_esg_snapshot is not a regular view"

    if ! view_count=$(psql_safe -tAc \
        "SELECT COUNT(*) FROM public.regional_esg_snapshot" 2>/dev/null); then
        error "regional_esg_snapshot query failed"
    fi

    [[ "$view_count" =~ ^[[:space:]]*[0-9]+[[:space:]]*$ ]] ||
        error "regional_esg_snapshot returned an invalid count"
}

existing_tables=$(scalar "
    SELECT COUNT(*)
    FROM information_schema.tables
    WHERE table_schema = 'public'
      AND table_type = 'BASE TABLE'
")

assert_equal "$existing_tables" "0" \
    "test requires an initially empty public schema"

printf '%s\n' "Running fresh upgrade"
python3 -m alembic upgrade head
assert_final_schema
success "fresh upgrade and schema assertions"

current_revision=$(scalar \
    "SELECT version_num FROM public.alembic_version LIMIT 1")
[[ -n "$current_revision" ]] ||
    error "alembic_version is empty after fresh upgrade"

printf '%s\n' "Running destructive downgrade"
python3 -m alembic downgrade 31e5cc432377

table_after_down=$(scalar "
    SELECT COUNT(*)
    FROM pg_class c
    JOIN pg_namespace n
      ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relname IN ('region_esg_scores', 'regional_esg_snapshot')
")
assert_equal "$table_after_down" "0" \
    "table or view remains after downgrade"

printf '%s\n' "Running re-upgrade"
python3 -m alembic upgrade head
assert_final_schema
success "downgrade and re-upgrade"

before_revision=$(scalar \
    "SELECT version_num FROM public.alembic_version LIMIT 1")

printf '%s\n' "Running idempotent upgrade"
python3 -m alembic upgrade head
assert_final_schema

after_revision=$(scalar \
    "SELECT version_num FROM public.alembic_version LIMIT 1")
assert_equal "$after_revision" "$before_revision" \
    "revision changed during idempotent upgrade"

heads=$(python3 -m alembic heads |
    sed -nE 's/^([[:xdigit:]]{12}).*/\1/p')

head_count=$(printf '%s\n' "$heads" |
    sed '/^[[:space:]]*$/d' |
    wc -l |
    tr -d '[:space:]')

assert_equal "$head_count" "1" \
    "expected exactly one Alembic head"

head_revision=$(printf '%s\n' "$heads" |
    sed -n '1p')

assert_equal "$after_revision" "$head_revision" \
    "database revision does not match Alembic head"

success "GAP-001 round-trip verified; database restored to head"
