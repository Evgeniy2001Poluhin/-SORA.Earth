#!/usr/bin/env bash
# shellcheck disable=SC2016
#   The mutation programs are literal text handed to a Python replacement, not
#   shell to be evaluated. Single quotes keep $VARIABLES inside them unexpanded,
#   so the anchors reach the script's source as written.
# Mutation testing for tools/wait_for.sh.
#
# "22 tests pass" is a statement about the tests. Each mutation below removes one
# property the tool exists for; the named test must fail. Two of these are
# defects the tool actually had before its tests were run.
#
# The tracked script is never modified. Mutants live in a temp directory.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ORIGINAL="$ROOT/tools/wait_for.sh"
TESTS="$ROOT/tests/test_wait_for.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"; pkill -P $$ 2>/dev/null' EXIT

PASS=0; FAIL=0

run_suite() {
    SCRIPT_UNDER_TEST="$1" bash "$TESTS" 2>&1
}

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
        printf '  ERROR  %-40s anchor not found — mutation never applied\n' "$name"
        FAIL=$((FAIL+1)); return
    fi

    # Captured, never piped. Under `set -o pipefail` the suite's non-zero exit --
    # which is exactly what a caught mutation produces -- would become the
    # pipeline's status and invert the verdict. That defect shipped once already,
    # in tools/mutation_backfill_periods.sh.
    local out
    out="$(run_suite "$mutant")"
    if printf '%s' "$out" | grep -q "FAIL.*$expect"; then
        printf '  caught %-40s → "%s" failed\n' "$name" "$expect"
        PASS=$((PASS+1))
    else
        printf '  MISSED %-40s → "%s" still passed\n' "$name" "$expect"
        FAIL=$((FAIL+1))
    fi
}

echo "== the unmodified tool passes its own suite =="
BASELINE="$(run_suite "$ORIGINAL")"
if printf '%s' "$BASELINE" | grep -q "failed: 0"; then
    printf '  ok     baseline is green\n'
else
    printf '  FAIL   baseline is not green; nothing below means anything\n'
    printf '%s\n' "$BASELINE" | tail -5
    FAIL=$((FAIL+1))
fi

echo
echo "== each property, removed =="

# The defect the suite found on its first run: the handler tidied up, returned,
# and the loop carried on waiting while the caller believed it had stopped.
#
# The first version of this mutation anchored on `trap cleanup EXIT` and appended
# TERM to it. That changed nothing: the `trap 'exit 143' TERM` four lines below
# still ran and still overrode it, so the mutant behaved correctly and the run
# reported MISSED. A mutation that does not do what its name says produces a
# verdict about nothing -- the anchor was found, the file was written, and the
# result was still meaningless.
run_mutation "a signal is tidied up but not obeyed" \
    "the runner is gone" \
    "trap 'exit 143' TERM||=>||trap cleanup TERM"

# A default deadline is one somebody chose for a different wait.
run_mutation "deadline becomes optional" \
    "usage error" \
    '[ -n "$DEADLINE" ] || die "--deadline is required||=>||DEADLINE="${DEADLINE:-600}"; [ -n "$DEADLINE" ] || die "--deadline is required'

# Without the clamp a 3s budget sleeps 30s, and an outer timeout kills the run
# before it can report why it stopped.
run_mutation "interval may overshoot the deadline" \
    "stopped at the deadline" \
    'if [ "$remaining" -lt "$INTERVAL" ]; then sleep "$remaining"; else sleep "$INTERVAL"; fi||=>||sleep "$INTERVAL"'

# The 13h39m loop: the process behind the predicate was already dead.
run_mutation "a dead subject is not noticed" \
    "exit status is 2" \
    'if [ -n "$PID" ] && ! kill -0 "$PID" 2>/dev/null; then||=>||if false; then'

# The five loops still polling results already obtained by another route.
run_mutation "cancellation is ignored" \
    "exit status is 3" \
    'if [ -n "$CANCEL" ] && [ -e "$CANCEL" ]; then||=>||if false; then'

echo
echo "  caught: $PASS   missed: $FAIL"
[ "$FAIL" -eq 0 ]
