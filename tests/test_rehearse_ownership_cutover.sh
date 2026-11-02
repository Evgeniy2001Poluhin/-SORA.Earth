#!/usr/bin/env bash
# The cutover rehearsal, run for real against a synthetic dump.
#
#   REHEARSAL_TEST_IMAGE=<release image> bash tests/test_rehearse_ownership_cutover.sh
#
# Needs docker, the release image built from this checkout, a postgres:16 and
# a postgres:15 image present locally, a clean checkout (the rehearsal refuses
# any other), and the model seed materialized. It creates only objects
# labelled sora.rehearsal.nonce or sora.rehearsal.fixture and checks after
# every case that none is left.
#
# Each case is a refusal or a failure path the rehearsal must take -- and one
# full run that must pass. A synthetic dump proves the mechanics: that the
# path from f4b7c2d91e06 through the accounts and ownership revisions runs,
# that every guard fires, that cleanup is confirmed. It proves nothing about
# production's timings; only the rehearsal on the production dump does.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO/scripts/rehearse_ownership_cutover.sh"
IMAGE="${REHEARSAL_TEST_IMAGE:-}"
PG16="${REHEARSAL_TEST_PG_IMAGE:-postgres:16}"
PG15="${REHEARSAL_TEST_PG15_IMAGE:-postgres:15}"
ONLY="${REHEARSAL_TEST_ONLY:-}"
# Limits small enough for a laptop's docker VM: the rehearsal refuses unless
# twice their sum is available, and a synthetic database needs little.
PG_MEM="${REHEARSAL_TEST_PG_MEMORY:-512m}"
APP_MEM="${REHEARSAL_TEST_APP_MEMORY:-1536m}"

PASS=0; FAIL=0
_report_tally() {
    local rc=$?
    echo
    echo "  passed: $PASS   failed: $FAIL"
    [ "$FAIL" -eq 0 ] || exit 1
    exit "$rc"
}
trap _report_tally EXIT
ok()    { PASS=$((PASS+1)); printf '  ok    %s\n' "$1"; }
bad()   { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 — expected [$3], got [$2]"; fi; }
has()   { if grep -qF -- "$2" "$OUT"; then ok "$1"; else bad "$1 — '$2' not in the output: $(grep REFUSED "$OUT" | head -1)"; fi; }
want()  { [ -z "$ONLY" ] || [[ " $ONLY " == *" $1 "* ]]; }

[ -n "$IMAGE" ] || { bad "REHEARSAL_TEST_IMAGE is not set; nothing can be rehearsed"; exit 1; }
docker image inspect "$IMAGE" >/dev/null 2>&1 || { bad "the release image $IMAGE is not present"; exit 1; }
docker image inspect "$PG16" >/dev/null 2>&1 || { bad "$PG16 is not present"; exit 1; }
SHA="$(git -C "$REPO" rev-parse HEAD)"
if [ -n "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]; then
    bad "the checkout has local changes; the rehearsal refuses that, so commit first"; exit 1
fi

T="$(mktemp -d "${TMPDIR:-/tmp}/sora-rehearsal-test.XXXXXX")"
T="$(cd "$T" && pwd -P)"
mkdir -p "$T/run" "$T/deployments" "$T/backup" "$T/bin" "$T/fault"
REAL_DOCKER="$(command -v docker)"
# A docker that injects one fault when its marker exists, and is the real one
# otherwise. Kept out of the PATH except in the cases that use it.
cat > "$T/bin/docker" <<STUB
#!/usr/bin/env bash
F="$T/fault"
REAL="$REAL_DOCKER"
if [ -f "\$F/fail_volume_ls_q" ] && [ "\${1-}" = volume ] && [ "\${2-}" = ls ] && [ "\${3-}" = -q ]; then
    echo "injected: volume ls fails" >&2; exit 1
fi
if [ -f "\$F/zero_inodes" ] && [[ " \$* " == *" sora.rehearsal.probe=space "* ]]; then
    "\$REAL" "\$@" | sed 's/^inodes_free=.*/inodes_free=0/'; exit "\${PIPESTATUS[0]}"
fi
if [ -f "\$F/fail_sampler" ] && [ "\${1-}" = exec ] && [[ " \$* " == *"-sampler "* ]]; then
    echo "injected: the sampler cannot connect" >&2; exit 1
fi
exec "\$REAL" "\$@"
STUB
chmod +x "$T/bin/docker"

cleanup_test() {
    # Anything a failed case left behind, so the next run starts clean -- and
    # the case that left it has already been reported by leftovers().
    "$REAL_DOCKER" ps -aq --filter label=sora.rehearsal.nonce | xargs -r "$REAL_DOCKER" rm -f >/dev/null 2>&1
    "$REAL_DOCKER" volume ls -q --filter label=sora.rehearsal.nonce | xargs -r "$REAL_DOCKER" volume rm >/dev/null 2>&1
    "$REAL_DOCKER" network ls -q --filter label=sora.rehearsal.nonce | xargs -r "$REAL_DOCKER" network rm >/dev/null 2>&1
    [ -n "${HOLDER:-}" ] && kill "$HOLDER" 2>/dev/null
    # Every case's output and report, when asked to keep them.
    if [ -n "${REHEARSAL_TEST_KEEP_DIR:-}" ]; then
        cp "$T"/out.* "$T"/fixture.log "$REHEARSAL_TEST_KEEP_DIR"/ 2>/dev/null
        cp "$T"/reports/*.json "$REHEARSAL_TEST_KEEP_DIR"/ 2>/dev/null
    fi
    chmod -R u+w "$T" 2>/dev/null
    rm -rf "$T"
}
trap 'cleanup_test; _report_tally' EXIT

echo "== a synthetic dump at f4b7c2d91e06 =="
DUMP="$T/dumps/synthetic.dump"
mkdir -p "$T/dumps"
if bash "$REPO/tests/rehearsal_synthetic_dump.sh" "$IMAGE" "$PG16" "$DUMP" "${REHEARSAL_TEST_ROWS:-3000}" f4b7c2d91e06 > "$T/fixture.log" 2>&1; then
    ok "the synthetic dump was built"
else
    bad "the synthetic dump could not be built: $(tail -5 "$T/fixture.log")"; exit 1
fi
DSHA="$( (sha256sum "$DUMP" 2>/dev/null || shasum -a 256 "$DUMP") | awk '{print $1}')"

N=0
# One rehearsal. Arguments after the first replace or extend the defaults.
rehearse() {
    N=$((N + 1))
    OUT="$T/out.$N"
    REPORT="$T/reports/r$N.json"
    mkdir -p "$T/reports"
    local start; start="$(date +%s)"
    env REHEARSAL_DEPLOY_LOCK="$T/run/deploy.lock" \
        REHEARSAL_DEPLOY_JOURNAL="$T/deployments/in-progress" \
        REHEARSAL_BACKUP_LOCK_DIR="$T/backup" \
        REHEARSAL_MONITOR_INTERVAL=2 \
        "${EXTRA_ENV[@]}" \
        bash "$SCRIPT" --dump "$DUMP" --dump-sha256 "$DSHA" --image "$IMAGE" --release-sha "$SHA" \
            --pg-image "$PG16" --memory "$PG_MEM" --app-memory "$APP_MEM" --app-workers 1 --cpus 2 \
            --disk-reserve-bytes 1073741824 --max-load 1000 --report "$REPORT" "$@" > "$OUT" 2>&1
    RC=$?
    ELAPSED=$(( $(date +%s) - start ))
}
EXTRA_ENV=(PATH="$PATH")
in_bg() {   # like rehearse, in the background; sets PID
    N=$((N + 1))
    OUT="$T/out.$N"
    REPORT="$T/reports/r$N.json"
    mkdir -p "$T/reports"
    BG_START="$(date +%s)"
    env REHEARSAL_DEPLOY_LOCK="$T/run/deploy.lock" \
        REHEARSAL_DEPLOY_JOURNAL="$T/deployments/in-progress" \
        REHEARSAL_BACKUP_LOCK_DIR="$T/backup" \
        REHEARSAL_MONITOR_INTERVAL=2 \
        "${EXTRA_ENV[@]}" \
        bash "$SCRIPT" --dump "$DUMP" --dump-sha256 "$DSHA" --image "$IMAGE" --release-sha "$SHA" \
            --pg-image "$PG16" --memory "$PG_MEM" --app-memory "$APP_MEM" --app-workers 1 --cpus 2 \
            --disk-reserve-bytes 1073741824 --max-load 1000 --report "$REPORT" "$@" > "$OUT" 2>&1 &
    PID=$!
}
wait_for() {   # pattern, seconds
    local i=0
    while [ "$i" -lt $(($2 * 10)) ]; do
        grep -qF -- "$1" "$OUT" 2>/dev/null && return 0
        kill -0 "$PID" 2>/dev/null || return 1
        sleep 0.1; i=$((i + 1))
    done
    return 1
}
finish_bg() { wait "$PID"; RC=$?; ELAPSED=$(( $(date +%s) - BG_START )); }

leftovers() {   # every object any rehearsal ever labelled, still present
    {
        "$REAL_DOCKER" ps -aq --filter label=sora.rehearsal.nonce
        "$REAL_DOCKER" volume ls -q --filter label=sora.rehearsal.nonce
        "$REAL_DOCKER" network ls -q --filter label=sora.rehearsal.nonce
        "$REAL_DOCKER" ps -aq --filter label=sora.rehearsal.fixture
        "$REAL_DOCKER" network ls -q --filter label=sora.rehearsal.fixture
    } | wc -l | tr -d ' '
}
after_each() {   # label
    check "$1: nothing of the rehearsal is left in docker" "$(leftovers)" "0"
    check "$1: the dump is untouched" \
        "$( (sha256sum "$DUMP" 2>/dev/null || shasum -a 256 "$DUMP") | awk '{print $1}')" "$DSHA"
    cleanup_partial
}
cleanup_partial() {
    "$REAL_DOCKER" ps -aq --filter label=sora.rehearsal.nonce | xargs -r "$REAL_DOCKER" rm -f >/dev/null 2>&1
    "$REAL_DOCKER" volume ls -q --filter label=sora.rehearsal.nonce | xargs -r "$REAL_DOCKER" volume rm >/dev/null 2>&1
    "$REAL_DOCKER" network ls -q --filter label=sora.rehearsal.nonce | xargs -r "$REAL_DOCKER" network rm >/dev/null 2>&1
    rm -f "$T/fault/"* "$T/deployments/in-progress"
}
report_field() {   # python expression over `doc`
    python3 -I - "$REPORT" "$1" <<'PY' 2>/dev/null || echo "no report"
import json, sys
doc = json.load(open(sys.argv[1]))
print(eval(sys.argv[2], {"doc": doc}))
PY
}
no_pg_started() { check "$1: no PostgreSQL was started" "$(grep -c 'a disposable PostgreSQL' "$OUT")" "0"; }

# ---------------------------------------------------------------- the pass
if want pass; then
echo "== three restores, a negative run, and a pass =="
# The deployment lock exists, as it does on the production host once any
# deployment has run, so the rehearsal takes and holds it.
: > "$T/run/deploy.lock"
rehearse
check "the rehearsal passes on the synthetic dump" "$RC" "0"
check "  the report says passed" "$(report_field 'doc["passed"]')" "True"
check "  three measured runs" "$(report_field 'len(doc["runs"])')" "3"
check "  from f4b7c2d91e06 to the ownership head" \
    "$(report_field 'sorted({(r["alembic_before"], r["alembic_after"]) for r in doc["runs"]})')" "[('f4b7c2d91e06', 'b7d41f2e9c35')]"
check "  the bootstrap created the accounts" "$(report_field 'sorted({r["users_after"] for r in doc["runs"]})')" "['3']"
check "  every run's sampler was believable" "$(report_field 'all(r["sampler"]["valid"] for r in doc["runs"])')" "True"
check "  and saw the migrator" "$(report_field 'all(r["sampler"]["migrator_observed"] > 0 for r in doc["runs"])')" "True"
check "  peak disk measured from samples" "$(report_field 'all((r["sampler"]["peak_extra_bytes"] or 0) > 0 for r in doc["runs"])')" "True"
check "  the backend started and was healthy each time" "$(report_field 'all(r["app_healthy"] for r in doc["runs"])')" "True"
check "  the stop window includes the backend's start" \
    "$(report_field 'all(r["stop_window_s"]["backend_start_to_healthy"] > 0 for r in doc["runs"])')" "True"
check "  control hashes are in the report" \
    "$(report_field 'all(len(r["tables"][t]["control_before"].split("|")) == 3 for r in doc["runs"] for t in r["tables"])')" "True"
check "  the negative run refused" "$(report_field 'doc["negative"]["migrate_rc"] != 0')" "True"
check "  and left both tables' content as it was" \
    "$(report_field 'all(v["control_before"] == v["control_after"] for v in doc["negative"]["tables"].values())')" "True"
check "  and the fingerprint" "$(report_field 'doc["negative"]["fingerprint_identical"]')" "True"
check "  the image was tied to the release" "$(report_field 'doc["release"]["sha"]')" "$SHA"
check "  PostgreSQL 16" "$(report_field 'doc["postgres"]["server_version_num"][:2]')" "16"
check "  cleanup confirmed" "$(report_field 'doc["cleanup"]["all_removed_and_confirmed"]')" "True"
check "  the dump's checksum after" "$(report_field 'doc["dump_sha256_after"]')" "$DSHA"
check "  the deployment lock was held" "$(report_field 'doc["coordination"]["deploy_lock_state"]')" "held by this rehearsal"
after_each "the pass"
[ -z "${REHEARSAL_TEST_KEEP_DIR:-}" ] || cp "$REPORT" "$REHEARSAL_TEST_KEEP_DIR/rehearsal-pass.json"
fi

# ---------------------------------------------------------------- refusals
if want refusals; then
echo "== refused before anything is measured =="
rehearse --runs 1 --report "$DUMP"
check "a --report that is the dump: refused" "$RC" "2"
has "  for that reason" "is the dump"
after_each "report is the dump"

ln -s "$DUMP" "$T/reports/link.json"
rehearse --report "$T/reports/link.json"
check "a --report that is a symlink to the dump: refused" "$RC" "2"
has "  for that reason" "is the dump"
after_each "report links the dump"

ln "$DUMP" "$T/reports/hard.json"
rehearse --report "$T/reports/hard.json"
check "a --report that is a hard link of the dump: refused" "$RC" "2"
has "  for that reason" "is the dump"
after_each "report hard-links the dump"

rehearse --release-sha ""
check "no release SHA: refused" "$RC" "2"
has "  for that reason" "--release-sha are required"
after_each "no release sha"

rehearse --release-sha 0000000000000000000000000000000000000000
check "a release SHA that is not this checkout: refused" "$RC" "2"
has "  for that reason" "not the release"
after_each "wrong release sha"

if docker image inspect "$PG15" >/dev/null 2>&1; then
    rehearse --pg-image "$PG15"
    check "PostgreSQL 15: refused" "$RC" "2"
    has "  for that reason" "not 16"
    after_each "postgres 15"
else
    bad "$PG15 is not present, so the version refusal was not exercised"
fi

python3 -I -c 'import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
time.sleep(120)' "$T/run/deploy.lock" &
HOLDER=$!
sleep 1
rehearse
kill "$HOLDER" 2>/dev/null; wait "$HOLDER" 2>/dev/null; HOLDER=""
check "a deployment holding its lock: refused" "$RC" "2"
has "  for that reason" "a deployment holds"
after_each "deploy lock held"

: > "$T/deployments/in-progress"
rehearse
check "an unfinished deployment journal: refused" "$RC" "2"
has "  for that reason" "an unfinished deployment is recorded"
after_each "deploy journal"

python3 -I -c 'import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
time.sleep(120)' "$T/backup/backup-sora_earth.lock" &
HOLDER=$!
sleep 1
rehearse
kill "$HOLDER" 2>/dev/null; wait "$HOLDER" 2>/dev/null; HOLDER=""
check "a backup holding its lock: refused" "$RC" "2"
has "  for that reason" "a backup is running"
after_each "backup lock held"

rehearse --max-load 0.01
check "a host above --max-load: refused" "$RC" "2"
has "  for that reason" "host load"
after_each "host load"

rehearse --disk-reserve-bytes 1000000000000000
check "a disk reserve the host cannot keep: refused" "$RC" "2"
has "  for that reason" "less the reserve is below 3x"
no_pg_started "disk reserve"
after_each "disk reserve"

: > "$T/fault/zero_inodes"
EXTRA_ENV=(PATH="$T/bin:$PATH")
rehearse
EXTRA_ENV=(PATH="$PATH")
check "an inode count of zero: refused" "$RC" "2"
has "  for that reason" "free inodes on the docker data root are unknown"
no_pg_started "zero inodes"
after_each "zero inodes"
fi

# ---------------------------------------------------------------- failures
if want failures; then
echo "== failures during the run, and of the cleanup =="

: > "$T/fault/fail_sampler"
EXTRA_ENV=(PATH="$T/bin:$PATH")
rehearse --runs 1
EXTRA_ENV=(PATH="$PATH")
check "a sampler that cannot connect fails the run" "$RC" "1"
check "  the breach names the sampler" \
    "$(report_field 'any("sampler is believable" in b for b in doc["breaches"])')" "True"
check "  not a pass with zero lock wait" "$(report_field 'doc["passed"]')" "False"
after_each "broken sampler"

rehearse --runs 1
check "one run is never a pass" "$RC" "1"
check "  the breach says why" "$(report_field 'any("at least 3" in b for b in doc["breaches"])')" "True"
after_each "one run"

: > "$T/fault/fail_volume_ls_q"
EXTRA_ENV=(PATH="$T/bin:$PATH")
rehearse --disk-reserve-bytes 1000000000000000
EXTRA_ENV=(PATH="$PATH")
check "an inventory that fails during cleanup: not confirmed" "$RC" "3"
has "  and said so" "CLEANUP NOT CONFIRMED"
check "  the report says why" "$(report_field 'doc["cleanup"]["why_not"] is not None')" "True"
after_each "inventory failure"

in_bg --runs 1
wait_for "run 1 (measure): migrate" 300 && sleep 0.5
kill -TERM "$PID" 2>/dev/null
finish_bg
check "a signal mid-run" "$RC" "143"
has "  is reported" "interrupted by SIGTERM"
check "  the report records the exit" "$(report_field 'doc["exit_status_before_cleanup"]')" "143"
check "  and is not a pass" "$(report_field 'doc["passed"]')" "False"
after_each "signal"

in_bg --runs 1
wait_for "run 1 (measure): restore" 300
: > "$T/deployments/in-progress"
finish_bg
check "a deployment beginning mid-run aborts it" "$RC" "5"
has "  and says why" "a deployment began"
check "  the report records it" "$(report_field 'doc["aborted"]["degraded"] is not None')" "True"
after_each "deployment began"

EXTRA_ENV=(PATH="$PATH" REHEARSAL_WALL_SECONDS=45)
in_bg --runs 3
finish_bg
EXTRA_ENV=(PATH="$PATH")
check "the wall clock stops the run" "$RC" "124"
check "  within the wall, cleanup included" "$([ "$ELAPSED" -le 50 ] && echo within || echo "took ${ELAPSED}s")" "within"
check "  the report records it" "$(report_field 'doc["aborted"]["timed_out"]')" "True"
after_each "wall clock"

in_bg --runs 1
wait_for "run 1 (measure): restore" 300
rm -rf "$T/reports"
finish_bg
check "a report that cannot be written is fatal" "$RC" "4"
has "  and said so" "REPORT NOT WRITTEN"
after_each "report not written"
fi
