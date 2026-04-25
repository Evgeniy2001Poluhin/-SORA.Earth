#!/bin/bash
BASE="http://localhost:8000/api/v1"
PASS=0; FAIL=0

check() {
  local name="$1"; local expected="$2"; local got="$3"
  if [[ "$got" == "$expected" ]]; then
    echo "PASS $name HTTP $got"; PASS=$((PASS+1))
  else
    echo "FAIL $name HTTP $got expected $expected"; FAIL=$((FAIL+1))
  fi
}

echo "-- DRIFT --"
check "drift_cold"        200 "$(curl -s -o /dev/null -w '%{http_code}' $BASE/mlops/drift)"
check "baseline_fit"      200 "$(curl -s -o /dev/null -w '%{http_code}' -X POST $BASE/mlops/drift/baseline/fit)"
check "simulate_0"        200 "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/mlops/drift/simulate?shift=0&n=80")"
check "drift_stable"      200 "$(curl -s -o /dev/null -w '%{http_code}' $BASE/mlops/drift)"
check "simulate_5"        200 "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/mlops/drift/simulate?shift=5&n=80")"
check "drift_detected"    200 "$(curl -s -o /dev/null -w '%{http_code}' $BASE/mlops/drift)"
check "baseline_delete"   200 "$(curl -s -o /dev/null -w '%{http_code}' -X DELETE $BASE/mlops/drift/baseline)"

echo "-- EXPLAIN --"
check "explain_global"    200 "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/explain/global?top_n=11&nsamples=10")"
check "explain_local"     200 "$(curl -s -o /dev/null -w '%{http_code}' -X POST $BASE/explain/local -H "Content-Type: application/json" -d '{"budget":100000,"co2_reduction":250,"social_impact":7,"duration_months":24}')"

echo "-- EXISTING --"
check "openapi"           200 "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/openapi.json)"
check "drift_analyze"     200 "$(curl -s -o /dev/null -w '%{http_code}' -X POST $BASE/drift/analyze)"
check "drift_features"    200 "$(curl -s -o /dev/null -w '%{http_code}' $BASE/drift/features/stats)"
check "model_drift"       200 "$(curl -s -o /dev/null -w '%{http_code}' $BASE/model/drift)"

echo
echo "=== PASS: $PASS  FAIL: $FAIL ==="
