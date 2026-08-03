#!/usr/bin/env bash
# shellcheck disable=SC2016
#   The mutation programs are literal text handed to a Python replacement, not
#   shell to be evaluated. Single quotes are required so $VARIABLES inside them
#   reach the script's source unexpanded -- expanding here would rewrite the
#   anchor and match nothing. File-wide because every mutation is such a string.
# Mutation testing for scripts/backup_local_daily.sh.
#
# Twenty-three passing tests say nothing about whether they discriminate. Each mutation
# below breaks one property on a temporary copy; the named test must fail. If it
# still passes, that test is decorative and this run says so.
#
# The tracked script is never modified. Mutants live in a temp directory and are
# deleted; none is ever committed.
set -uo pipefail


ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ORIGINAL="$ROOT/scripts/backup_local_daily.sh"
TESTS="$ROOT/tests/test_backup_local_daily.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

PASS=0; FAIL=0

# name | expected-failing-test-substring | replacement, as "old||=>||new"
#
# Not a sed program, whatever the shape suggests: the third argument is split on
# ||=>|| and handed to Python's str.replace, so both halves are literal text. Any
# sed syntax written here -- addresses, s///, backreferences -- would be matched
# character for character and silently find nothing.
run_mutation() {
    local name="$1" expect="$2" program="$3"
    local mutant="$WORK/mutant.sh"
    cp "$ORIGINAL" "$mutant"
    if ! python3 - "$mutant" "$program" <<'PY'
import sys
path, prog = sys.argv[1], sys.argv[2]
old, new = prog.split("||=>||")
s = open(path).read()
if old not in s:
    sys.exit("MUTATION ANCHOR NOT FOUND")
open(path, "w").write(s.replace(old, new, 1))
PY
    then
        printf '  ERROR  %-42s anchor not found — mutation never applied\n' "$name"
        FAIL=$((FAIL+1)); return
    fi

    local out
    out="$(SCRIPT_UNDER_TEST="$mutant" bash "$TESTS" 2>&1)"
    if echo "$out" | grep -q "FAIL.*$expect"; then
        printf '  caught %-42s → "%s" failed\n' "$name" "$expect"
        PASS=$((PASS+1))
    else
        printf '  MISSED %-42s → "%s" still passed\n' "$name" "$expect"
        FAIL=$((FAIL+1))
    fi
}

echo "== mutation testing: each break must be caught =="

run_mutation "no flock" "nothing published while locked" \
'flock -n -E 75 9 || lock_rc=$?||=>||lock_rc=0'

run_mutation "validation skipped" "the failure names validation" \
'"${DC[@]}" pg_restore --list "$CONTAINER_TMP" > /dev/null 2>&1 \
    || fail "pg_restore --list rejected the dump"||=>||true'

run_mutation "published before validation" "nothing published" \
'"${DC[@]}" pg_restore --list "$CONTAINER_TMP" > /dev/null 2>&1 \
    || fail "pg_restore --list rejected the dump"||=>||"${DC[@]}" cat "$CONTAINER_TMP" > "$FINAL" 2>/dev/null
    "${DC[@]}" pg_restore --list "$CONTAINER_TMP" > /dev/null 2>&1 \
    || fail "pg_restore --list rejected the dump"'

run_mutation "retention before the dump" "the old backup survives a failed run" \
'log "starting dump of $DB_NAME"||=>||find "$BACKUP_DIR" -name "${DB_NAME}_*.dump" -mtime "+$KEEP_DAYS" -delete 2>/dev/null
log "starting dump of $DB_NAME"'

run_mutation "password in argv" "no password flag in any invocation" \
'"${DC[@]}" pg_dump -U "$DB_USER"||=>||"${DC[@]}" pg_dump --password -U "$DB_USER"'

run_mutation "checksum not written" "checksum written" \
'echo "$SHA  $(basename "$FINAL")" > "$FINAL.sha256"||=>||true'

# Deliberately not tested: replacing `|| fail` on pg_dump with `|| touch` leaves
# the run failing anyway, because the empty file is caught by the size guard two
# steps later. The mutant is equivalent from outside -- non-zero exit, nothing
# published -- so no test can distinguish it, and demanding one would be asking
# for a test of an internal path rather than a behaviour. Verified by running it.

run_mutation "empty dump accepted" "the failure names emptiness" \
'[ -s "$TMP" ] || fail "dump is empty after copying out"||=>||true'

# The size comparison was untested until the stub gained a way to truncate the
# copy: it always reported the same size on both sides, so the two could never
# disagree. A guard that cannot fail is indistinguishable from no guard, and this
# mutation is what makes the difference visible.
run_mutation "truncated copy accepted" "the failure names the size gap" \
'[ "$CONTAINER_SIZE" = "$HOST_SIZE" ] \
    || fail "copied $HOST_SIZE bytes, container reported $CONTAINER_SIZE"||=>||true'

echo
echo "  caught: $PASS   missed: $FAIL"
[ "$FAIL" -eq 0 ]
