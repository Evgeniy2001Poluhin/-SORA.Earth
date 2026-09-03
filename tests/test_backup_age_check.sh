#!/usr/bin/env bash
# Behavioural checks for the backup freshness check.
#
# In shell because the thing under test is shell; reimplementing it in Python
# would test the reimplementation. Follows the structure of
# tests/test_backup_local_daily.sh.
#
# Nothing here touches PostgreSQL or docker: the script only looks at files, so
# a temporary directory is the whole seam.
#
# The case that matters most is the empty one. "the newest dump is older than N
# hours" evaluated over zero dumps has no newest, and the obvious spelling of it
# compares an empty string and quietly succeeds -- so a host whose schedule was
# never enabled reports healthy. That is the failure this script exists for, and
# it is asserted first.
set -uo pipefail

SCRIPT="${SCRIPT_UNDER_TEST:-$(cd "$(dirname "$0")/.." && pwd)/scripts/backup_age_check.sh}"

# GNU stat and GNU find. Production and CI are Linux; a portable version would
# test a different script than the one that runs.
if [ "$(uname -s)" != "Linux" ]; then
    echo "SKIP: requires GNU stat/find. Run in CI or:"
    echo "  docker run --rm -v \"\$PWD:/w\" -w /w ubuntu:24.04 bash tests/test_backup_age_check.sh"
    exit 0
fi

PASS=0
FAIL=0
ok()   { PASS=$((PASS+1)); printf '  ok    %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 — expected [$3], got [$2]"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

run() { # run <dir> [extra env assignments...]
    local dir="$1"; shift
    env BACKUP_DIR="$dir" DB_NAME=sora_earth "$@" bash "$SCRIPT" >"$TMP/out" 2>&1
    echo $?
}

# --- absence ---------------------------------------------------------------

rc=$(run "$TMP/does-not-exist")
check "a missing backup directory fails" "$rc" 1
grep -q 'does not exist' "$TMP/out" && ok "and says the directory is missing" \
    || bad "the message does not name the cause: $(cat "$TMP/out")"

mkdir -p "$TMP/empty"
rc=$(run "$TMP/empty")
check "an empty backup directory fails" "$rc" 1
grep -q '0 files examined' "$TMP/out" \
    && ok "and prints the count it examined" \
    || bad "no denominator in the message: $(cat "$TMP/out")"

mkdir -p "$TMP/wrongname"
touch "$TMP/wrongname/other_db_20260101T000000Z.dump"
rc=$(run "$TMP/wrongname")
check "dumps for another database do not count" "$rc" 1

# --- age -------------------------------------------------------------------

mkdir -p "$TMP/fresh"
touch "$TMP/fresh/sora_earth_20260903T033005Z.dump"
rc=$(run "$TMP/fresh")
check "a dump written now passes" "$rc" 0
grep -q 'is 0h old' "$TMP/out" && ok "and reports the age it measured" \
    || bad "no age in the message: $(cat "$TMP/out")"

mkdir -p "$TMP/stale"
touch -d '30 hours ago' "$TMP/stale/sora_earth_20260901T033005Z.dump"
rc=$(run "$TMP/stale")
check "a 30h-old dump fails against the 26h limit" "$rc" 1
grep -q '30h old' "$TMP/out" && ok "and reports the measured age" \
    || bad "the age is not reported: $(cat "$TMP/out")"

mkdir -p "$TMP/edge"
touch -d '25 hours ago' "$TMP/edge/sora_earth_20260902T033005Z.dump"
rc=$(run "$TMP/edge")
check "25h passes: the limit has slack for a slow run" "$rc" 0

# The newest wins, not the first found. A directory keeps a week of dumps, so
# picking any other one reports a stale backup on a healthy host.
mkdir -p "$TMP/many"
touch -d '6 days ago'  "$TMP/many/sora_earth_20260828T033005Z.dump"
touch -d '30 hours ago' "$TMP/many/sora_earth_20260901T033005Z.dump"
touch                   "$TMP/many/sora_earth_20260903T033005Z.dump"
rc=$(run "$TMP/many")
check "the newest of several dumps decides" "$rc" 0
grep -q 'of 3 dump' "$TMP/out" && ok "and the count is reported" \
    || bad "count missing: $(cat "$TMP/out")"

# --- the threshold is honoured ---------------------------------------------

rc=$(run "$TMP/stale" MAX_AGE_HOURS=48)
check "a wider limit accepts the same file" "$rc" 0

rc=$(run "$TMP/fresh" MAX_AGE_HOURS=0)
check "a zero limit still accepts a dump written this hour" "$rc" 0

# --- a broken alert hook must not mask the finding -------------------------

printf '#!/bin/sh\nexit 3\n' > "$TMP/hook.sh"
chmod +x "$TMP/hook.sh"
rc=$(run "$TMP/stale" BACKUP_ALERT_HOOK="$TMP/hook.sh")
check "a failing alert hook does not change the verdict" "$rc" 1

printf '#!/bin/sh\necho "$1 $2" >> %s/hook.log\n' "$TMP" > "$TMP/hook2.sh"
chmod +x "$TMP/hook2.sh"
rc=$(run "$TMP/stale" BACKUP_ALERT_HOOK="$TMP/hook2.sh")
grep -q 'backup_stale' "$TMP/hook.log" 2>/dev/null \
    && ok "the hook is called with the event name" \
    || bad "the hook was not called: $(cat "$TMP/hook.log" 2>/dev/null)"

echo
echo "  passed $PASS, failed $FAIL"
[ "$FAIL" -eq 0 ]
