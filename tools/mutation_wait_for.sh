#!/usr/bin/env bash
# shellcheck disable=SC2016
#   The mutation programs are literal Python text handed to a replacement, not
#   shell to be evaluated. Single quotes keep $ and interpolation-looking text
#   unexpanded, so the anchors reach the source as written.
# Mutation testing for tools/wait_for.py.
#
# "the tests pass" is a statement about the tests. Each mutation removes one
# property the tool exists for; the named test must fail. Six of them are
# defects this branch actually shipped -- five in the shell implementation that
# preceded this one, one here.
#
# Results are reported in three categories, not two. A "missed" that lumps a
# surviving mutant together with one that never applied hides a broken harness
# inside a coverage number: an earlier run reported 6 missed, of which 2 were
# anchors that no longer matched code I had rewritten.
#
# The tracked file is never modified. Mutants live in a temp directory, each
# anchor must match exactly once, and each mutant is parsed before use -- a
# mutant that does not compile tests nothing.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ORIGINAL="$ROOT/tools/wait_for.py"
TESTS="$ROOT/tests/test_wait_for.sh"
WRAPPER="$ROOT/tools/wait_for.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK:?}"; pkill -P $$ 2>/dev/null' EXIT

KILLED=0; SURVIVED=0; INVALID=0

# The suite invokes the wrapper, which execs a fixed path. A mutant needs its
# own wrapper pointing at itself.
mutant_wrapper() {
    printf '#!/usr/bin/env bash\nexec python3 %s "$@"\n' "$1" > "$WORK/wrapper.sh"
    chmod +x "$WORK/wrapper.sh"
    printf '%s' "$WORK/wrapper.sh"
}

run_suite() {
    SCRIPT_UNDER_TEST="$1" WAIT_FOR_ONLY="${2-}" bash "$TESTS" 2>&1
}

# name | section the mutation must break | assertion that must fail | program
run_mutation() {
    local name="$1" section="$2" expect="$3" program="$4"
    local mutant="$WORK/mutant.py"
    cp "$ORIGINAL" "$mutant"
    if ! python3 - "$mutant" "$program" <<'PY'
import ast, sys
path, prog = sys.argv[1], sys.argv[2]
old, new = prog.split("||=>||")
s = open(path).read()
if s.count(old) != 1:
    sys.exit("ANCHOR MATCHED %d TIMES, EXPECTED 1" % s.count(old))
s = s.replace(old, new, 1)
open(path, "w").write(s)
try:
    ast.parse(s)
except SyntaxError as exc:
    sys.exit("MUTANT DOES NOT PARSE: %s" % exc)
PY
    then
        printf '  invalid  %-42s mutation never applied\n' "$name"
        INVALID=$((INVALID+1)); return
    fi

    # Captured, never piped. Under `set -o pipefail` the suite's non-zero exit --
    # exactly what a killed mutant produces -- would become the pipeline's
    # status and invert the verdict. That defect shipped once already.
    local out wrapper
    wrapper="$(mutant_wrapper "$mutant")"
    out="$(run_suite "$wrapper" "$section")"
    if printf '%s' "$out" | grep -q "FAIL.*$expect"; then
        printf '  killed   %-42s → "%s" failed\n' "$name" "$expect"
        KILLED=$((KILLED+1))
    else
        printf '  survived %-42s → "%s" still passed\n' "$name" "$expect"
        SURVIVED=$((SURVIVED+1))
    fi
}

echo "== the unmodified tool passes its own suite =="
BASELINE="$(run_suite "$WRAPPER")"
if printf '%s' "$BASELINE" | grep -q "failed: 0"; then
    printf '  ok       baseline is green\n'
else
    printf '  FAIL     baseline is not green; nothing below means anything\n'
    printf '%s\n' "$BASELINE" | tail -5
    INVALID=$((INVALID+1))
fi

echo
echo "== each property, removed =="

# A default deadline is one somebody chose for a different wait.
run_mutation "deadline becomes optional" \
    "a deadline is required" \
    "usage error" \
    '    if args.deadline is None:||=>||    args.deadline = args.deadline or 600
    if False:'

# The blocker review found in the shell version: with the predicate
# unsupervised, the deadline is invisible until it returns of its own accord.
run_mutation "the deadline does not bound the predicate" \
    "the deadline bounds the predicate" \
    "bounded by the deadline" \
    '            while proc.poll() is None:
                outcome = guard()
                if outcome is not None:
                    stop_group(proc, pgid, graceful=True)
                    return outcome
                time.sleep(POLL)||=>||            while proc.poll() is None:
                time.sleep(POLL)'

# A predicate that forks leaves a grandchild that killing the leader never
# reaches. This is what the process group is for.
run_mutation "only the leader is stopped" \
    "the whole tree goes" \
    "and it is gone too" \
    '            os.killpg(pgid, sig)||=>||            os.kill(pgid, sig)'

# The leader can exit 0 having left something running, and the next attempt
# overwrites the group id.
run_mutation "no sweep after an attempt" \
    "a satisfied wait leaves nothing behind" \
    "and success swept it up" \
    '            stop_group(proc, pgid, graceful=True)
            proc, pgid = None, None||=>||            proc, pgid = None, None'

# An exited leader stays a zombie until reaped, and a zombie answers killpg(0).
# Without the reap the grace period runs its full length on every stop.
run_mutation "the grace period never reaps the leader" \
    "signal during a long predicate" \
    "and promptly" \
    '            if proc is not None:
                proc.poll()
            if not group_exists(pgid):||=>||            if not group_exists(pgid):'

# The whole point of start_new_session: without it the predicate shares the
# waiter's group, and every group signal reaches the waiter itself.
run_mutation "the predicate shares the caller's group" \
    "its own process group" \
    "the predicate is in a group of its own" \
    '                    start_new_session=True,||=>||                    start_new_session=False,'

# The gap between attempts, unguarded: a 3s budget sleeps 30s.
run_mutation "interval may overshoot the deadline" \
    "the interval never overshoots" \
    "stopped at the deadline" \
    '            resume = time.monotonic() + args.interval
            while time.monotonic() < resume:
                outcome = guard()
                if outcome is not None:
                    return outcome
                time.sleep(POLL)||=>||            time.sleep(args.interval)'

# The 13h39m loop: the process behind the predicate was already dead.
run_mutation "a dead subject is not noticed" \
    "a subject that exits ends the wait" \
    "exit status is 2" \
    '        if args.pid is not None:||=>||        if False:'

# The five loops still polling results already obtained by another route.
run_mutation "cancellation is ignored" \
    "a wait can be cancelled" \
    "exit status is 3" \
    '        if args.cancel and os.path.exists(args.cancel):||=>||        if False:'

echo
echo "  killed: $KILLED   survived: $SURVIVED   invalid: $INVALID"
[ "$SURVIVED" -eq 0 ] && [ "$INVALID" -eq 0 ]
