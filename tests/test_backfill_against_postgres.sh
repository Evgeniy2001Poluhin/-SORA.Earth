#!/usr/bin/env bash
# The backfill's write path, against a real PostgreSQL.
#
# The unit tests cover the matching rule. They cannot cover what the review
# found: a recheck that never reaches the rows it exists to correct, and a date
# left behind when a verdict is withdrawn. Both live in SQL, and both were
# invisible to every test that did not run one.
#
# The sequence is the one that matters: a row decided, then re-decided against a
# different answer, then decided again -- with as_of_date checked at each step.
set -uo pipefail

PASS=0; FAIL=0
ok()    { PASS=$((PASS+1)); printf '  ok    %s\n' "$1"; }
bad()   { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 — expected [$3], got [$2]"; fi; }

command -v docker >/dev/null 2>&1 || { echo "SKIP: docker is not available"; exit 0; }

REPO="$(cd "$(dirname "$0")/.." && pwd)"
C="backfill-pg-$$"
PORT=55470
trap 'docker rm -f "$C" >/dev/null 2>&1' EXIT

docker run -d --name "$C" -p "$PORT:5432" \
    -e POSTGRES_USER=sora -e POSTGRES_PASSWORD=sora2026 -e POSTGRES_DB=sora_earth \
    --tmpfs /var/lib/postgresql/data:rw -e PGDATA=/var/lib/postgresql/data/pg \
    postgres:16 >/dev/null 2>&1

for _ in $(seq 1 45); do
    docker exec "$C" pg_isready -U sora -q >/dev/null 2>&1 && break
    sleep 2
done
docker exec "$C" pg_isready -U sora -q >/dev/null 2>&1 || { echo "  FAIL  the stand never came up"; exit 1; }

export DATABASE_URL="postgresql://sora:sora2026@localhost:$PORT/sora_earth"
( cd "$REPO" && python3 -m alembic upgrade head >/dev/null 2>&1 )

q() { docker exec "$C" psql -U sora -d sora_earth -tAc "$1" 2>/dev/null | tr -d '[:space:]'; }

q "INSERT INTO country_indicator_history
     (country_iso3, indicator_code, source, value, fetched_at)
   VALUES ('SAU','NY.GDP.PCAP.CD','world_bank', 34536.66, now())" >/dev/null

echo "== a verdict is recorded with its provenance =="
q "UPDATE country_indicator_history
     SET as_of_date='2025-01-01', period_status='recovered_inferred',
         period_run_id='run-1', period_rule_version='value-match/1',
         period_candidates=1" >/dev/null
check "the row is decided"        "$(q "SELECT period_status FROM country_indicator_history")" "recovered_inferred"
check "and dated"                 "$(q "SELECT as_of_date::date FROM country_indicator_history")" "2025-01-01"

echo "== a recheck can reach a decided row =="
# The defect: the selection carried `as_of_date IS NULL`, so a decided row --
# which by definition has a date -- was never selected, and a corrected rule
# could not correct anything.
check "an old-rule row is selectable" \
    "$(q "SELECT count(*) FROM country_indicator_history
          WHERE (period_status IS NULL OR period_rule_version='value-match/1')
            AND (period_status IS NOT NULL OR as_of_date IS NULL)")" "1"

echo "== withdrawing a verdict withdraws its date =="
# COALESCE kept the old date when the new verdict had none, leaving a row that
# says "several candidates" while carrying the date of a single one. The CHECK
# constraint refuses that pairing, which is how the assignment is proved.
q "UPDATE country_indicator_history
     SET as_of_date=NULL, period_status='ambiguous',
         period_run_id='run-2', period_rule_version='value-match/2',
         period_candidates=2" >/dev/null
check "the verdict changed"       "$(q "SELECT period_status FROM country_indicator_history")" "ambiguous"
check "and the date is gone"      "$(q "SELECT coalesce(as_of_date::text,'none') FROM country_indicator_history")" "none"

OUT="$(docker exec "$C" psql -U sora -d sora_earth -tAc \
    "UPDATE country_indicator_history SET as_of_date='2025-01-01' WHERE period_status='ambiguous'" 2>&1)"
check "a date on an ambiguous row is refused" \
    "$(echo "$OUT" | grep -qc 'violates check constraint' && echo yes || echo no)" "yes"

echo "== and a corrected rule can decide it again =="
q "UPDATE country_indicator_history
     SET as_of_date='2024-01-01', period_status='recovered_inferred',
         period_run_id='run-3', period_rule_version='value-match/3',
         period_candidates=1" >/dev/null
check "decided again"             "$(q "SELECT period_status FROM country_indicator_history")" "recovered_inferred"
check "with the new date"         "$(q "SELECT as_of_date::date FROM country_indicator_history")" "2024-01-01"
check "and the run is identified" "$(q "SELECT period_run_id FROM country_indicator_history")" "run-3"

echo "== a verdict without a date is refused =="
OUT="$(docker exec "$C" psql -U sora -d sora_earth -tAc \
    "UPDATE country_indicator_history SET as_of_date=NULL WHERE period_status='recovered_inferred'" 2>&1)"
check "recovered_inferred must keep its date" \
    "$(echo "$OUT" | grep -qc 'violates check constraint' && echo yes || echo no)" "yes"

echo
echo "  passed: $PASS   failed: $FAIL"
[ "$FAIL" -eq 0 ]
