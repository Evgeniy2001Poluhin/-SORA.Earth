#!/usr/bin/env bash
# Behavioural checks for the daily local dump.
#
# In shell because the thing under test is shell; reimplementing it in Python
# would test the reimplementation. Same reasoning as tests/test_backup_lock.sh,
# whose structure this follows.
#
# The script reaches PostgreSQL only through `docker compose exec -T postgres`,
# so a stub `docker` on PATH is the whole seam. Nothing here talks to a database
# or a container, and no test needs one -- the point is the script's own
# decisions: what it publishes, what it refuses to publish, and what it deletes.
#
# The integration test that does use a real PostgreSQL lives separately; this
# file is about the branches a live database cannot reach on demand, such as a
# dump that passes and a validation that fails.
set -uo pipefail

# Overridable so mutation testing can point these at a deliberately broken copy
# without touching the tracked script. tools/mutation_backup_local.sh does that;
# nothing else sets it.
SCRIPT="${SCRIPT_UNDER_TEST:-$(cd "$(dirname "$0")/.." && pwd)/scripts/backup_local_daily.sh}"

# Linux only, deliberately. Production and CI are Linux, and flock -- which the
# script uses and which one of these tests holds -- does not exist on macOS.
# Making the test portable would test a different script than the one that runs.
# A local skip says so out loud rather than passing vacuously.
if [ "$(uname -s)" != "Linux" ]; then
    echo "SKIP: these tests require Linux (flock). Run them in CI or a container:"
    echo "  docker run --rm -v \"\$PWD:/w\" -w /w ubuntu:24.04 \\"
    echo "    sh -c 'apt-get update -qq && apt-get install -y -qq util-linux >/dev/null && bash tests/test_backup_local_daily.sh'"
    exit 0
fi
PASS=0
FAIL=0

ok()   { PASS=$((PASS+1)); printf '  ok    %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 — expected [$3], got [$2]"; fi; }

# --- the stub -------------------------------------------------------------
#
# Behaviour is driven by files in $STUB_DIR, so each test sets up the failure it
# wants without editing the stub.
#
#   fail_isready   pg_isready returns non-zero
#   fail_dump      pg_dump returns non-zero
#   fail_list      pg_restore --list returns non-zero
#   dump_bytes     how many bytes the fake dump contains
#   argv_log       every argument the stub ever saw, for the credential check

new_sandbox() {
    SANDBOX="$(mktemp -d)"
    STUB_DIR="$SANDBOX/stub"
    BACKUPS="$SANDBOX/backups"
    mkdir -p "$STUB_DIR/bin" "$BACKUPS"
    echo 64 > "$STUB_DIR/dump_bytes"

    cat > "$STUB_DIR/bin/docker" <<'STUB'
#!/usr/bin/env bash
# Stands in for `docker compose -f ... exec -T postgres <cmd> ...`.
echo "$*" >> "$STUB_DIR/argv_log"
shift $(( $# > 0 ? 0 : 0 ))
# Drop everything up to and including the service name.
while [ $# -gt 0 ]; do
    case "$1" in
        postgres) shift; break ;;
        *) shift ;;
    esac
done
cmd="${1:-}"; shift || true
case "$cmd" in
    pg_isready)
        if [ -f "$STUB_DIR/fail_isready" ]; then
            # Consume the timeout the caller asked for, the way a real probe
            # against an unreachable server does. Exiting instantly made the
            # budget test measure only the script's sleeps, so dropping the
            # per-probe -t cap would not have shown up. With no -t the wait is
            # deliberately long enough to overrun any budget these tests set.
            t=10
            while [ $# -gt 0 ]; do
                [ "$1" = "-t" ] && { t="$2"; break; }
                shift
            done
            sleep "$t"
            exit 2
        fi
        exit 0 ;;
    pg_dump)
        [ -f "$STUB_DIR/fail_dump" ] && exit 1
        # --file <path> is the last pair the script passes.
        out=""
        while [ $# -gt 0 ]; do
            [ "$1" = "--file" ] && { out="$2"; break; }
            shift
        done
        head -c "$(cat "$STUB_DIR/dump_bytes")" /dev/zero > "$out"
        exit 0 ;;
    pg_restore)
        [ -f "$STUB_DIR/fail_list" ] && exit 1
        exit 0 ;;
    cat)
        # truncate_copy holds how many bytes survive the copy out. stat still
        # reports the true size, so the two disagree exactly as they would if the
        # pipe were cut mid-transfer. Without this the stub always made them
        # agree, and the size check could be deleted with every test still green.
        if [ -f "$STUB_DIR/truncate_copy" ]; then
            head -c "$(cat "$STUB_DIR/truncate_copy")" "$1"
        else
            cat "$1"
        fi
        exit 0 ;;
    stat)
        # stat -c %s <file>
        wc -c < "$3" | tr -d ' '; exit 0 ;;
    rm)
        command rm -f "$2" 2>/dev/null; exit 0 ;;
    *)
        exit 0 ;;
esac
STUB
    chmod +x "$STUB_DIR/bin/docker"
}

run_script() {
    # Exported, not prefixed. The stub is invoked by the script -- a grandchild of
    # this shell -- and must see STUB_DIR in its own environment. A prefix
    # assignment would not be visible to the PATH expansion on the same line
    # either (ShellCheck SC2097/SC2098); it worked only because the export above
    # happened to be there.
    export STUB_DIR
    export PATH="$STUB_DIR/bin:$PATH"
    BACKUP_DIR="$BACKUPS" \
    LOCK_FILE="$SANDBOX/lock" \
    COMPOSE_FILE="$SANDBOX/compose.yml" \
    PROJECT_DIR="$SANDBOX" \
    DB_NAME="testdb" \
    DB_USER="testuser" \
    KEEP_DAYS="${KEEP_DAYS:-7}" \
    READY_TIMEOUT="${READY_TIMEOUT:-4}" \
        bash "$SCRIPT" > "$SANDBOX/out" 2>&1
    echo $?
}

dumps()   { find "$BACKUPS" -name '*.dump' 2>/dev/null | wc -l | tr -d ' '; }
tmpfiles(){ find "$BACKUPS" -name '*.tmp'  2>/dev/null | wc -l | tr -d ' '; }

echo "== the stub actually intercepts =="
# Before anything else. Every test below is worthless if the real docker is being
# called, or if the stub runs and cannot record -- both would look like a pass.
new_sandbox
export STUB_DIR
export PATH="$STUB_DIR/bin:$PATH"
docker compose -f x exec -T postgres pg_isready >/dev/null 2>&1
check "the stub is on PATH and ran"  "$([ -f "$STUB_DIR/argv_log" ] && echo yes || echo no)" "yes"
check "it recorded the invocation"   "$(grep -qc 'pg_isready' "$STUB_DIR/argv_log" 2>/dev/null && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

echo "== a successful dump is published atomically =="
new_sandbox
rc="$(run_script)"
check "exit status is 0"                "$rc" "0"
check "one dump published"              "$(dumps)" "1"
check "no .tmp left behind"             "$(tmpfiles)" "0"
check "checksum written"                "$(find "$BACKUPS" -name '*.sha256' | wc -l | tr -d ' ')" "1"
rm -rf "$SANDBOX"

echo "== a failed dump publishes nothing =="
new_sandbox
touch "$STUB_DIR/fail_dump"
rc="$(run_script)"
check "exit status is non-zero"         "$([ "$rc" != 0 ] && echo yes || echo no)" "yes"
check "nothing published"               "$(dumps)" "0"
check "no .tmp left behind"             "$(tmpfiles)" "0"
rm -rf "$SANDBOX"

echo "== a dump that fails validation publishes nothing =="
new_sandbox
touch "$STUB_DIR/fail_list"
rc="$(run_script)"
check "exit status is non-zero"         "$([ "$rc" != 0 ] && echo yes || echo no)" "yes"
check "nothing published"               "$(dumps)" "0"
check "the failure names validation"    "$(grep -qi 'pg_restore --list rejected' "$SANDBOX/out" && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

echo "== an empty dump is not published =="
# The stub is told to produce zero bytes: pg_dump succeeds, validation succeeds,
# and what comes out is nothing. No other case exercises this -- mutation testing
# found it by removing the guard and having every test still pass.
new_sandbox
echo 0 > "$STUB_DIR/dump_bytes"
rc="$(run_script)"
check "exit status is non-zero"         "$([ "$rc" != 0 ] && echo yes || echo no)" "yes"
check "nothing published"               "$(dumps)" "0"
check "the failure names emptiness"     "$(grep -qi 'empty' "$SANDBOX/out" && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

echo "== retention does not run when the dump fails =="
new_sandbox
# An old completed backup that must survive a failed run.
old="$BACKUPS/testdb_20200101T000000Z.dump"
touch -d '30 days ago' "$old" 2>/dev/null || touch -t 202001010000 "$old"
touch "$STUB_DIR/fail_dump"
KEEP_DAYS=7 rc="$(run_script)"
check "the old backup survives a failed run" "$([ -f "$old" ] && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

echo "== retention removes old copies only after a success =="
new_sandbox
old="$BACKUPS/testdb_20200101T000000Z.dump"
touch -d '30 days ago' "$old" 2>/dev/null || touch -t 202001010000 "$old"
KEEP_DAYS=7 rc="$(run_script)"
check "exit status is 0"                     "$rc" "0"
check "the old copy is gone"                 "$([ -f "$old" ] && echo yes || echo no)" "no"
check "the new one is there"                 "$(dumps)" "1"
rm -rf "$SANDBOX"

echo "== a held lock stops a second run without failing =="
new_sandbox
(
    exec 9>"$SANDBOX/lock"
    flock -n 9
    rc="$(run_script)"
    check "exit status is 0"                 "$rc" "0"
    check "nothing published while locked"   "$(dumps)" "0"
    check "the skip is logged"               "$(grep -qi 'holds the lock' "$SANDBOX/out" && echo yes || echo no)" "yes"
)
rm -rf "$SANDBOX"

echo "== a truncated copy out is not published =="
# pg_dump succeeds, validation succeeds, and the transfer to the host loses half
# the bytes. Only the size comparison catches it: the short file still lists
# correctly, so pg_restore --list would accept it and a checksum computed after
# the truncation would agree with itself.
new_sandbox
echo 32 > "$STUB_DIR/truncate_copy"   # of the 64 the dump contains
rc="$(run_script)"
check "exit status is non-zero"         "$([ "$rc" != 0 ] && echo yes || echo no)" "yes"
check "nothing published"               "$(dumps)" "0"
check "no .tmp left behind"             "$(tmpfiles)" "0"
check "the failure names the size gap"  "$(grep -qiE 'copied .* bytes, container reported' "$SANDBOX/out" && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

echo "== an unready database fails within its budget =="
# The stub now consumes the -t it is given, so this measures the real wait rather
# than the script's sleeps alone. The allowance is one second for clock
# granularity: with a 4s budget the script spends about 2s probing and 2s
# waiting. Dropping the per-probe cap makes the stub wait its 10s default and
# overruns this, which is the regression the loose six-second bound permitted.
new_sandbox
touch "$STUB_DIR/fail_isready"
start=$SECONDS
READY_TIMEOUT=4 rc="$(run_script)"
elapsed=$(( SECONDS - start ))
check "exit status is non-zero"         "$([ "$rc" != 0 ] && echo yes || echo no)" "yes"
check "within the stated budget"        "$([ "$elapsed" -le 5 ] && echo yes || echo no)" "yes"
check "it actually waited"              "$([ "$elapsed" -ge 3 ] && echo yes || echo no)" "yes"
check "nothing published"               "$(dumps)" "0"
rm -rf "$SANDBOX"

echo "== no credentials reach argv =="
new_sandbox
run_script > /dev/null
# The stub records every argument it was ever given. A password appearing here
# would appear in /proc/<pid>/cmdline too, readable by every process on the host.
check "no password flag in any invocation" \
    "$(grep -qiE '(^|[[:space:]])(-W|--password|PGPASSWORD)' "$STUB_DIR/argv_log" && echo yes || echo no)" "no"
rm -rf "$SANDBOX"

echo
echo "  passed: $PASS   failed: $FAIL"
[ "$FAIL" -eq 0 ]
