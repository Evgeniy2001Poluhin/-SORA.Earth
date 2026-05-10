#!/usr/bin/env bash
set -eu
TS=$(date +%Y%m%d_%H%M%S)
OUT="output/loadtest_v2_${TS}"
mkdir -p "$OUT"

HOST="${LOADTEST_HOST:-http://127.0.0.1:8000}"
USERS="${LOADTEST_USERS:-50}"
SPAWN="${LOADTEST_SPAWN:-10}"
DURATION="${LOADTEST_DURATION:-3m}"

echo "→ Target: $HOST  users=$USERS  spawn=$SPAWN/s  duratiTION"
locust -f loadtest/locustfile_v2.py \
  --host "$HOST" \
  --headless \
  --csv "$OUT/stats" \
  --html "$OUT/report.html" \
  --run-time "$DURATION" \
  --users "$USERS" --spawn-rate "$SPAWN" \
  --only-summary

echo ""
echo "→ Report:  $OUT/report.html"
echo "→ CSV:     $OUT/stats_stats.csv"
