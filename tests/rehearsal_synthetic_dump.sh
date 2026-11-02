#!/usr/bin/env bash
# A synthetic production dump for exercising scripts/rehearse_ownership_cutover.sh.
#
#   tests/rehearsal_synthetic_dump.sh <release-image> <postgres-image> <out.dump> [rows] [revision]
#
# A disposable PostgreSQL migrated by the release image's own alembic to
# <revision> -- by default f4b7c2d91e06, where production stood before the
# accounts and ownership revisions -- then `rows` invented rows in evaluations
# and predictions_log, every column filled from its type, and a custom-format
# pg_dump of the result. Synthetic data only; the container, its network and
# its volume are removed on every exit and their absence is checked.
#
# What a dump made this way cannot stand in for is said in docs/DEPLOY.md
# ("Rehearsing the cutover"): it has production's schema, not its data.
set -euo pipefail

IMAGE="${1:?release image}"
PG_IMAGE="${2:?postgres image}"
OUT="${3:?output path}"
ROWS="${4:-5000}"
REVISION="${5:-f4b7c2d91e06}"
case "$ROWS" in ''|*[!0-9]*) echo "rows must be a number" >&2; exit 2 ;; esac
[[ "$REVISION" =~ ^[0-9a-f]{12}$ ]] || { echo "revision must be a 12-character alembic id" >&2; exit 2; }
[ ! -e "$OUT" ] || { echo "$OUT exists; refusing to overwrite it" >&2; exit 2; }

TAG="sora-rehearsal-fixture-$(od -An -N6 -tx1 /dev/urandom | tr -d ' \n')"
NET="$TAG-net"; PG="$TAG-pg"; PW="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"

cleanup() {
    local rc=$?
    docker rm -fv "$PG" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
    local left
    left="$(docker ps -aq --filter "label=sora.rehearsal.fixture=$TAG"; docker network ls -q --filter "label=sora.rehearsal.fixture=$TAG")" \
        || { echo "fixture cleanup: docker could not be asked" >&2; exit 3; }
    if [ -n "$left" ]; then echo "fixture cleanup: left behind: $left" >&2; exit 3; fi
    [ "$rc" -eq 0 ] || rm -f "$OUT"
    exit "$rc"
}
trap cleanup EXIT

docker network create --internal --label "sora.rehearsal.fixture=$TAG" "$NET" >/dev/null
docker run -d --name "$PG" --label "sora.rehearsal.fixture=$TAG" --network "$NET" \
    -e POSTGRES_USER=sora -e POSTGRES_PASSWORD="$PW" -e POSTGRES_DB=sora_earth "$PG_IMAGE" >/dev/null
i=0
until docker exec "$PG" pg_isready -U sora -h 127.0.0.1 >/dev/null 2>&1; do
    i=$((i + 1)); [ "$i" -lt 120 ] || { echo "the fixture postgres never became ready" >&2; exit 1; }
    sleep 0.5
done
# initdb's temporary server answers on the socket only; one more TCP check.
sleep 1
docker exec "$PG" pg_isready -U sora -h 127.0.0.1 >/dev/null

docker run --rm --network "$NET" --label "sora.rehearsal.fixture=$TAG" \
    -e DATABASE_URL="postgresql+psycopg://sora:$PW@$PG:5432/sora_earth" \
    --entrypoint alembic "$IMAGE" upgrade "$REVISION" >/dev/null
got="$(docker exec "$PG" psql -U sora -d sora_earth -Atc 'SELECT version_num FROM alembic_version')"
[ "$got" = "$REVISION" ] || { echo "the fixture is at '$got', not $REVISION" >&2; exit 1; }

# Every column but the id filled from its type, so a content hash over the
# original columns has something to notice.
docker exec -i "$PG" psql -X -v ON_ERROR_STOP=1 -U sora -d sora_earth -q -v rows="$ROWS" <<'SQL'
SELECT set_config('fixture.rows', :'rows', false);
DO $$
DECLARE
    t text;
    cols text;
    exprs text;
    n int := current_setting('fixture.rows')::int;
BEGIN
    FOREACH t IN ARRAY ARRAY['evaluations', 'predictions_log'] LOOP
        SELECT string_agg(quote_ident(column_name), ', ' ORDER BY ordinal_position),
               string_agg(CASE
                   WHEN data_type IN ('integer', 'bigint', 'smallint') THEN '(g % 1000)'
                   WHEN data_type IN ('numeric', 'double precision', 'real') THEN '(g * 1.5 % 1000)'
                   WHEN data_type = 'boolean' THEN '(g % 2 = 0)'
                   WHEN data_type LIKE 'timestamp%' THEN '(timestamp ''2026-01-01'' + g * interval ''1 minute'')'
                   WHEN data_type = 'date' THEN '(date ''2026-01-01'' + (g % 300))'
                   WHEN data_type IN ('json', 'jsonb') THEN format('(''{"g": '' || g || ''}'')::%s', data_type)
                   WHEN data_type = 'uuid' THEN 'md5(g::text)::uuid'
                   WHEN character_maximum_length IS NOT NULL
                       THEN format('left(''s'' || g, %s)', character_maximum_length)
                   ELSE '(''syn-'' || g)'
               END, ', ' ORDER BY ordinal_position)
          INTO cols, exprs
          FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = t AND column_name <> 'id';
        EXECUTE format('INSERT INTO public.%I (%s) SELECT %s FROM generate_series(1, %s) g', t, cols, exprs, n);
    END LOOP;
END $$;
SQL
docker exec "$PG" psql -U sora -d sora_earth -Atc \
    "SELECT 'evaluations=' || (SELECT count(*) FROM evaluations) || ' predictions_log=' || (SELECT count(*) FROM predictions_log)"
docker exec "$PG" pg_dump -U sora -Fc sora_earth > "$OUT"
[ -s "$OUT" ] || { echo "the dump is empty" >&2; exit 1; }
echo "wrote $OUT ($(wc -c < "$OUT" | tr -d ' ') bytes) at $REVISION"
