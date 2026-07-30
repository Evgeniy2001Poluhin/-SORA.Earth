#!/usr/bin/env bash
# Behavioural checks for the backup lock.
#
# Written in shell because the thing under test is shell; reimplementing it in
# Python would test the reimplementation. Two techniques reach branches that
# would otherwise need root or a second platform:
#
#   * _have_flock is overridden to force the directory backend, so the staleness
#     and reclaim logic is covered on Linux too, where flock exists and would
#     otherwise hide it.
#   * _stat_owner is overridden to report a foreign uid for one path, so the
#     ownership refusals are covered without chown.
#
# Both are shell-function overrides inside this process. Production has no way
# to reach them.
set -uo pipefail
cd "$(dirname "$0")/.."
LIB=scripts/backup_lock.sh

pass=0; fail=0; skip=0
ok()   { printf 'ok   %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf 'FAIL %s: %s\n' "$1" "${2:-}"; fail=$((fail+1)); }
note() { printf 'skip %s: %s\n' "$1" "$2"; skip=$((skip+1)); }

# Sourcing must not change our options. This is the regression guard for a real
# defect: while this library set `set -e`, `acquire_backup_lock` returning 75
# exited the caller before it could report the skip.
before="$-"
source "$LIB"
[ "$before" = "$-" ] \
  && ok "sourcing the library leaves the caller's shell options alone" \
  || bad "sourcing the library leaves the caller's shell options alone" "$before -> $-"

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

# ---------------------------------------------------------------- runtime dir
good="$WORK/good"; mkdir -p "$good"; chmod 700 "$good"
validate_runtime_dir "$good" >/dev/null 2>&1 \
  && ok "a 0700 directory we own is accepted" \
  || bad "a 0700 directory we own is accepted"

for mode in 777 750 705 770; do
    d="$WORK/m$mode"; mkdir -p "$d"; chmod "$mode" "$d"
    validate_runtime_dir "$d" >/dev/null 2>&1 \
      && bad "mode $mode is refused" "it was accepted" \
      || ok "mode $mode is refused"
done

ln -s "$good" "$WORK/link"
validate_runtime_dir "$WORK/link" >/dev/null 2>&1 \
  && bad "a symlinked runtime directory is refused" "it was followed" \
  || ok "a symlinked runtime directory is refused"

validate_runtime_dir "$WORK/missing" >/dev/null 2>&1 \
  && bad "a missing runtime directory is refused" "it was accepted" \
  || ok "a missing runtime directory is refused"

# A foreign uid, via a stat override rather than chown.
(
    _stat_owner() { echo 999999; }
    err="$(validate_runtime_dir "$good" 2>&1)"; rc=$?
    if [ $rc -eq 0 ]; then
        echo "__RESULT__ accepted"
    elif printf '%s' "$err" | grep -q 'owned by uid'; then
        echo "__RESULT__ refused-for-ownership"
    else
        echo "__RESULT__ refused-for-another-reason: $err"
    fi
) > "$WORK/owner" 2>/dev/null
owner_result="$(sed -n 's/^__RESULT__ //p' "$WORK/owner")"
case "$owner_result" in
    refused-for-ownership) ok "a runtime directory owned by another uid is refused" ;;
    *) bad "a runtime directory owned by another uid is refused" "$owner_result" ;;
esac

# ------------------------------------------------------------------ fail closed
( unset BACKUP_RUNTIME_DIR RUNTIME_DIRECTORY
  SORA_ENV=production resolve_runtime_dir >/dev/null 2>&1 ) \
  && bad "production without a runtime directory fails closed" "it guessed one" \
  || ok "production without a runtime directory fails closed"

( BACKUP_RUNTIME_DIR="$WORK/m777" SORA_ENV=production resolve_runtime_dir >/dev/null 2>&1 ) \
  && bad "production with a world-writable runtime directory fails closed" "accepted" \
  || ok "production with a world-writable runtime directory fails closed"

# -------------------------------------------------------------------- lock file
export BACKUP_RUNTIME_DIR="$good"
passwd_before="$(wc -c < /etc/passwd)"
ln -sf /etc/passwd "$good/sym.lock"
acquire_backup_lock sym >/dev/null 2>&1 \
  && bad "a symlinked lock file is refused" "it was used" \
  || ok "a symlinked lock file is refused"
[ "$(wc -c < /etc/passwd)" = "$passwd_before" ] \
  && ok "the symlink target was not truncated" \
  || bad "the symlink target was not truncated" "size changed"
rm -f "$good/sym.lock"

: > "$good/foreign.lock"
(
    _stat_owner() { case "$1" in *foreign.lock) echo 999999;; *) id -u;; esac; }
    acquire_backup_lock foreign >/dev/null 2>&1; echo "rc=$?"
) > "$WORK/lf_owner" 2>/dev/null
[ "$(cat "$WORK/lf_owner")" = "rc=1" ] \
  && ok "a lock file owned by another uid is refused" \
  || bad "a lock file owned by another uid is refused" "$(cat "$WORK/lf_owner")"
rm -f "$good/foreign.lock"

# --------------------------------------------------- exclusion, both backends
run_exclusion_suite() {  # <label> <force_mkdir:0|1>
    local label="$1" force="$2" name="excl-$2" line holder after
    local pre="cd '$PWD'; source '$LIB'; export BACKUP_RUNTIME_DIR='$good';"
    [ "$force" = 1 ] && pre="$pre _have_flock() { return 1; };"

    ( eval "$pre"
      acquire_backup_lock "$name" >/dev/null 2>&1
      rc=$?
      if [ $rc -ne 0 ]; then echo "first=$rc"; exit 0; fi
      # A nested shell, so the holder is a live process while the rival probes.
      second=$(bash -c "$pre acquire_backup_lock '$name' >/dev/null 2>&1; echo \$?")
      echo "first=0 second=$second backend=$BACKUP_LOCK_BACKEND"
      release_backup_lock
    ) > "$WORK/ex-$force" 2>/dev/null

    line="$(head -1 "$WORK/ex-$force")"
    case "$line" in
      "first=0 second=75 "*) ok "$label: the holder keeps it, a rival gets 75 ($line)" ;;
      *) bad "$label: the holder keeps it, a rival gets 75" "$line" ;;
    esac

    # After the holder is killed outright, the next run must proceed.
    bash -c "$pre acquire_backup_lock '$name-crash' >/dev/null 2>&1; sleep 30" \
        >/dev/null 2>&1 &
    holder=$!
    sleep 2
    kill -9 $holder 2>/dev/null; wait $holder 2>/dev/null
    after=$( ( eval "$pre"
               acquire_backup_lock "$name-crash" >/dev/null 2>&1; echo $?
               release_backup_lock ) 2>/dev/null | head -1 )
    [ "$after" = "0" ] \
      && ok "$label: a lock left by a killed process is reclaimed" \
      || bad "$label: a lock left by a killed process is reclaimed" "rc=$after"
}

if _have_flock; then
    run_exclusion_suite "flock" 0
    run_exclusion_suite "directory backend" 1
else
    note "flock suite" "no flock on this platform"
    run_exclusion_suite "directory backend" 0
fi

# The fallback must announce itself. A silent one is how a missing binary got
# reported as contention, and a schedule looked healthy while backing up nothing.
msg=$( ( _have_flock() { return 1; }
         acquire_backup_lock announce 2>&1 >/dev/null
         release_backup_lock ) )
printf '%s' "$msg" | grep -qi 'flock is unavailable' \
  && ok "the directory fallback says so on stderr" \
  || bad "the directory fallback says so on stderr" "said: $msg"

# -------------------------------------------------------------- the call site
# Code only. Anchors that matched comments have passed while the code was wrong
# twice already in this branch.
code="$(grep -v '^[[:space:]]*#' scripts/backup_run.sh)"
printf '%s\n' "$code" | grep -q 'acquire_backup_lock .* || lock_rc=\$?' \
  && ok "backup_run.sh captures the lock status errexit-safely" \
  || bad "backup_run.sh captures the lock status errexit-safely" "a bare call exits 75 first"
printf '%s\n' "$code" | grep -q 'case \$lock_rc in' \
  && ok "backup_run.sh branches on the captured status, not \$?" \
  || bad "backup_run.sh branches on the captured status, not \$?"
printf '%s\n' "$code" | grep -q '/tmp/[^ ]*\.lock' \
  && bad "no lock path under /tmp survives in the runner" "found one" \
  || ok "no lock path under /tmp survives in the runner"
printf '%s\n' "$(grep -v '^[[:space:]]*#' "$LIB")" | grep -q '^[[:space:]]*set -e' \
  && bad "the library sets no shell options" "it does" \
  || ok "the library sets no shell options"

printf '\n%d passed, %d failed, %d skipped\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
