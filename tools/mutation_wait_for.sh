#!/usr/bin/env bash
# shellcheck disable=SC2016
#   The mutation programs are literal text handed to a Python replacement, not
#   shell to be evaluated. Single quotes keep $VARIABLES inside them unexpanded,
#   so the anchors reach the script's source as written.
# Mutation testing for tools/wait_for.sh.
#
# "the tests pass" is a statement about the tests. Each mutation below removes
# one property the tool exists for; the named test must fail. Four of them are
# defects this tool actually shipped with: a signal handler that tidied up and
# kept waiting, a deadline that did not bound the predicate, a kill that reached
# only the direct child, and a sweep skipped when the leader died first.
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
    SCRIPT_UNDER_TEST="$1" WAIT_FOR_ONLY="${2-}" bash "$TESTS" 2>&1
}

# name | section the mutation must break | assertion that must fail | program
#
# The section is named so the run costs seconds rather than minutes: repeating
# the whole suite once per mutation would be ~8 minutes to answer ten yes/no
# questions; narrowed, the run is 232s.
# The baseline below still runs all of it -- narrowing that would leave every
# section no mutation targets unproven.
run_mutation() {
    local name="$1" section="$2" expect="$3" program="$4"
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
    out="$(run_suite "$mutant" "$section")"
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
    "a signal stops it" \
    "the runner is gone" \
    "trap 'exit 143' TERM||=>||trap cleanup TERM"

# A default deadline is one somebody chose for a different wait.
run_mutation "deadline becomes optional" \
    "a deadline is required" \
    "usage error" \
    '[ -n "$DEADLINE" ] || die "--deadline is required||=>||DEADLINE="${DEADLINE:-600}"; [ -n "$DEADLINE" ] || die "--deadline is required'

# Without the guard in the gap between attempts, a 3s budget sleeps 30s, and an
# outer timeout kills the run before it can report why it stopped.
run_mutation "interval may overshoot the deadline" \
    "the interval never overshoots" \
    "stopped at the deadline" \
    '    resume=$(( $(date +%s) + INTERVAL ))
    while [ "$(date +%s)" -lt "$resume" ]; do
        guard
        sleep "$POLL"
    done||=>||    sleep "$INTERVAL"'

# The blocker review found. With the predicate unsupervised, the deadline, the
# cancellation and the subject are all invisible until it returns of its own
# accord -- so `--deadline 3 --until "sleep 20; false"` takes 20 seconds and
# then reports the deadline as though it had been enforced.
run_mutation "the deadline does not bound the predicate" \
    "the deadline bounds the predicate" \
    "bounded by the deadline" \
    '    while kill -0 "$predicate_pid" 2>/dev/null; do
        guard
        sleep "$POLL"
    done||=>||    while kill -0 "$predicate_pid" 2>/dev/null; do
        sleep "$POLL"
    done'

# A predicate that forks leaves a grandchild that killing the direct child never
# reaches. Both signals have to be group-wide: leaving the KILL group-wide would
# still collect the tree and hide the loss of the TERM.
run_mutation "only the direct child is stopped" \
    "the whole tree goes" \
    "and it is gone too" \
    '    kill -TERM -- "-$predicate_pgid" 2>/dev/null

    local ticks=0
    while kill -0 -- "-$predicate_pgid" 2>/dev/null && [ "$ticks" -lt "$GRACE_TICKS" ]; do
        sleep "$POLL"
        ticks=$((ticks + 1))
    done

    kill -KILL -- "-$predicate_pgid" 2>/dev/null||=>||    kill -TERM "$predicate_pgid" 2>/dev/null

    local ticks=0
    while kill -0 "$predicate_pgid" 2>/dev/null && [ "$ticks" -lt "$GRACE_TICKS" ]; do
        sleep "$POLL"
        ticks=$((ticks + 1))
    done

    kill -KILL "$predicate_pgid" 2>/dev/null'

# The leak review found after the deadline fix. Keying the sweep on the leader's
# liveness lets a predicate that backgrounds work and exits hand back a running
# child -- and the tree test cannot see it, because its predicate ends in `wait`
# so the leader never dies first.
run_mutation "a dead leader skips the group sweep" \
    "leaves nothing behind" \
    "and success swept it up" \
    '    [ -n "$predicate_pgid" ] || return 0||=>||    [ -n "$predicate_pgid" ] || return 0
    if ! kill -0 "$predicate_pid" 2>/dev/null; then predicate_pgid=""; return 0; fi'

# Without a sweep after each attempt, the next attempt overwrites the group id
# and the previous attempt'"'"'s children become unreachable.
run_mutation "no sweep between attempts" \
    "does not leak into the next" \
    "none of their children survive" \
    '    stop_predicate

    [ "$predicate_status" -eq 0 ]||=>||    [ "$predicate_status" -eq 0 ]'

# The tool's own precondition. Unchecked, a platform where `set -m` does not
# give a private group turns every group signal into a no-op that matches
# nothing, and the sweep leaks in silence rather than refusing.
run_mutation "the private group is assumed, not checked" \
    "without its own process group" \
    "refused with EX_OSERR" \
    '    if [ -n "$observed" ] && [ "$observed" != "$predicate_pid" ]; then||=>||    if false; then'

# The 13h39m loop: the process behind the predicate was already dead.
run_mutation "a dead subject is not noticed" \
    "a subject that exits ends the wait" \
    "exit status is 2" \
    'if [ -n "$PID" ] && ! kill -0 "$PID" 2>/dev/null; then||=>||if false; then'

# The five loops still polling results already obtained by another route.
run_mutation "cancellation is ignored" \
    "a wait can be cancelled" \
    "exit status is 3" \
    'if [ -n "$CANCEL" ] && [ -e "$CANCEL" ]; then||=>||if false; then'

echo
echo "  caught: $PASS   missed: $FAIL"
[ "$FAIL" -eq 0 ]
