#!/usr/bin/env bash
# Behavioural tests for tools/wait_for.sh.
#
# Almost all refusals, because almost all of the value is refusals: a wait with
# no deadline, a subject that died, a cancellation, an interval that would
# overshoot the budget. Each stands for something that actually happened.
#
# The outcomes are checked by exit code and by elapsed time together. A test that
# only asserts "non-zero" cannot tell a deadline from a dead subject, and those
# call for opposite responses -- retry versus stop.
set -uo pipefail

SCRIPT="${SCRIPT_UNDER_TEST:-$(cd "$(dirname "$0")/.." && pwd)/tools/wait_for.sh}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"; pkill -P $$ 2>/dev/null' EXIT

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok    %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 — expected [$3], got [$2]"; fi; }

# One section at a time, when asked. The suite takes 41 seconds; the mutation
# run repeats it once per mutation, and eight full passes is five and a half
# minutes to answer eight yes/no questions. WAIT_FOR_ONLY selects by substring,
# and an empty value matches everything -- so the default is still the whole
# suite, and only the mutation tool narrows it.
ONLY="${WAIT_FOR_ONLY:-}"
want() {
    case "$1" in
        *"$ONLY"*) printf '\n== %s ==\n' "$1"; return 0 ;;
    esac
    return 1
}

# Elapsed seconds of a run, alongside its exit code.
run() {
    local start end
    start="$(date +%s)"
    bash "$SCRIPT" "$@" >"$WORK/out" 2>&1
    STATUS=$?
    end="$(date +%s)"
    ELAPSED=$((end - start))
}

if want "a condition already true returns at once"; then
run --deadline 60 --interval 1 --until 'true'
check "exit status is 0"        "$STATUS" "0"
check "it did not sleep first"  "$( [ "$ELAPSED" -le 2 ] && echo yes || echo no )" "yes"

fi

if want "a condition that becomes true is waited for"; then
( sleep 3; : > "$WORK/appeared" ) &
run --deadline 60 --interval 1 --until "test -e $WORK/appeared"
check "exit status is 0"        "$STATUS" "0"
check "it actually waited"      "$( [ "$ELAPSED" -ge 2 ] && echo yes || echo no )" "yes"

fi

if want "a deadline is required"; then
run --interval 1 --until 'true'
check "usage error"             "$STATUS" "64"
check "the reason is named"     "$(grep -qc 'deadline is required' "$WORK/out" && echo yes || echo no)" "yes"

fi

if want "a deadline that passes is its own outcome"; then
run --deadline 3 --interval 1 --until 'false'
check "exit status is 75"       "$STATUS" "75"
# 75, not 1. A caller that cannot tell a timeout from a permanent failure either
# retries what will never succeed or gives up on what only needed longer.
check "within its own budget"   "$( [ "$ELAPSED" -le 6 ] && echo yes || echo no )" "yes"
check "it reports the deadline" "$(grep -qc 'deadline of 3s' "$WORK/out" && echo yes || echo no)" "yes"

fi

if want "the interval never overshoots the deadline"; then
# 3s budget, 30s interval: without the clamp this sleeps 30s past its own budget,
# and an outer timeout kills it before it can say why it stopped.
run --deadline 3 --interval 30 --until 'false'
check "exit status is 75"       "$STATUS" "75"
check "stopped at the deadline" "$( [ "$ELAPSED" -le 6 ] && echo yes || echo no )" "yes"

fi

if want "a subject that exits ends the wait"; then
# The 13h39m loop: the run behind it had been killed by its own watchdog, and the
# wait carried on polling a file that could never appear.
sleep 2 & SUBJECT=$!
run --deadline 60 --interval 1 --pid "$SUBJECT" --until 'false'
check "exit status is 2"        "$STATUS" "2"
check "not held to the deadline" "$( [ "$ELAPSED" -le 8 ] && echo yes || echo no )" "yes"
check "it names the subject"    "$(grep -qc "subject $SUBJECT exited" "$WORK/out" && echo yes || echo no)" "yes"

fi

if want "a wait can be cancelled"; then
# The answer arriving by another route: five loops were left polling results
# already obtained and already used.
( sleep 2; : > "$WORK/stop" ) &
run --deadline 60 --interval 1 --cancel "$WORK/stop" --until 'false'
check "exit status is 3"        "$STATUS" "3"
check "not held to the deadline" "$( [ "$ELAPSED" -le 8 ] && echo yes || echo no )" "yes"

fi

if want "a predicate that can never be true is not special-cased"; then
# A broken predicate (this session: a jq expression with unbalanced quotes) is
# indistinguishable from one that is merely false, so the deadline is what has to
# catch it -- which is why the deadline is mandatory.
run --deadline 3 --interval 1 --until 'this-command-does-not-exist'
check "exit status is 75"       "$STATUS" "75"

fi

if want "the deadline bounds the predicate, not just the gap between attempts"; then
# 20s, not 60. The correct tool leaves after ~2s either way, but a mutant with
# the guard removed runs the predicate to completion -- so this number is the
# price of every mutation that has to hang to prove the defect is real. 60
# turned the mutation run into four and a half minutes.
# The defect review found. The first version ran the predicate in the foreground
# and looked at the clock only after it returned, so this took 20s at a 3s
# deadline and then reported the deadline as though it had been enforced.
#
# Every case above uses a predicate that returns immediately -- which is exactly
# the shape where the defect cannot appear. That is why the suite was green
# while the tool's main contract was broken.
run --deadline 2 --interval 1 --until 'sleep 20; false'
check "exit status is 75"       "$STATUS" "75"
check "bounded by the deadline" "$( [ "$ELAPSED" -le 8 ] && echo yes || echo no )" "yes"

fi

if want "a running predicate does not block cancellation or the subject"; then
( sleep 2; : > "$WORK/stop2" ) &
run --deadline 60 --interval 1 --cancel "$WORK/stop2" --until 'sleep 20; false'
check "cancelled mid-predicate"  "$STATUS" "3"
check "not held by the predicate" "$( [ "$ELAPSED" -le 8 ] && echo yes || echo no )" "yes"

fi

if want "a slow predicate that does finish in time still succeeds"; then
# The deadline must bound the wait without truncating work that fits inside it.
run --deadline 20 --interval 1 --until 'sleep 2; true'
check "exit status is 0"        "$STATUS" "0"

fi

if want "the whole tree goes, not only the direct child"; then
# A predicate that forks leaves a grandchild that killing the child never
# reaches. This is what the process group is for.
rm -f "$WORK/grandchild"
run --deadline 2 --interval 1 --until "sh -c 'sleep 20 & echo \$! > $WORK/grandchild; wait'"
check "exit status is 75"       "$STATUS" "75"
GRANDCHILD="$(cat "$WORK/grandchild" 2>/dev/null)"
check "a grandchild was created" "$( [ -n "$GRANDCHILD" ] && echo yes || echo no )" "yes"
sleep 1
check "and it is gone too" \
    "$(kill -0 "$GRANDCHILD" 2>/dev/null && echo alive || echo gone)" "gone"

fi

if want "a satisfied wait leaves nothing behind either"; then
# The leak the tree test above cannot see. Its predicate ends in `wait`, so the
# leader lives as long as the child does -- and stop_predicate's early return
# for a dead leader is never reached. Here the leader exits immediately and the
# child outlives it.
#
# Measured before the fix: waiter exited 0 after 0s, `sleep 25` still running.
rm -f "$WORK/orphan.pid"
run --deadline 30 --interval 1 \
    --until "sh -c 'sleep 20 & echo \$! > $WORK/orphan.pid; exit 0'"
check "exit status is 0"        "$STATUS" "0"
ORPHAN="$(cat "$WORK/orphan.pid" 2>/dev/null)"
check "a child was left running" "$( [ -n "$ORPHAN" ] && echo yes || echo no )" "yes"
sleep 1
check "and success swept it up" \
    "$(kill -0 "$ORPHAN" 2>/dev/null && echo alive || echo gone)" "gone"
kill -9 "$ORPHAN" 2>/dev/null
fi

if want "a failed attempt does not leak into the next one"; then
# Same shape, failing. Without a sweep between attempts the group id is
# overwritten by the next attempt and the previous one's children become
# unreachable -- one orphan per attempt, for as long as the wait runs.
rm -f "$WORK/orphan2.pid"
run --deadline 3 --interval 1 \
    --until "sh -c 'sleep 20 & echo \$! >> $WORK/orphan2.pid; exit 1'"
check "exit status is 75"       "$STATUS" "75"
LEAKED=0
while read -r pid; do
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && LEAKED=$((LEAKED + 1))
done < "$WORK/orphan2.pid"
check "attempts were made"      "$( [ "$(wc -l < "$WORK/orphan2.pid")" -ge 1 ] && echo yes || echo no )" "yes"
check "none of their children survive" "$LEAKED" "0"
while read -r pid; do kill -9 "$pid" 2>/dev/null; done < "$WORK/orphan2.pid"
fi

if want "a signal during a long predicate stops both"; then
rm -f "$WORK/pred.pid"
bash "$SCRIPT" --deadline 120 --interval 1 \
    --until "echo \$\$ > $WORK/pred.pid; sleep 20" >/dev/null 2>&1 &
LONG=$!
sleep 3
PREDPID="$(cat "$WORK/pred.pid" 2>/dev/null)"
check "the predicate is running" \
    "$( [ -n "$PREDPID" ] && kill -0 "$PREDPID" 2>/dev/null && echo yes || echo no )" "yes"
kill -TERM "$LONG" 2>/dev/null
sleep 3
check "the waiter is gone"    "$(kill -0 "$LONG" 2>/dev/null && echo alive || echo gone)" "gone"
check "the predicate is gone" "$(kill -0 "$PREDPID" 2>/dev/null && echo alive || echo gone)" "gone"

fi

if want "nothing is left running"; then
BEFORE="$(pgrep -P $$ 2>/dev/null | wc -l | tr -d ' ')"
run --deadline 3 --interval 1 --until 'false'
sleep 1
AFTER="$(pgrep -P $$ 2>/dev/null | wc -l | tr -d ' ')"
check "no children survive the run" "$AFTER" "$BEFORE"

fi

if want "a signal stops it, rather than being tidied up and ignored"; then
# SIGTERM, not SIGINT. Bash sets SIGINT to SIG_IGN for asynchronous commands
# started without job control, and a trap cannot be installed on a signal
# inherited as ignored -- so a SIGINT case here would be testing the harness's
# own environment, not the script. Measured both ways before settling on this:
# TERM stops it, INT does not, with or without `bash -m`.
#
# The trap on INT stays in the script for the interactive case, where a person
# presses Ctrl-C and the signal reaches the process group. That path is not
# covered here, and saying so is better than a test that appears to cover it.
#
# What is covered is the defect that was here: `trap cleanup INT TERM` replaced
# the default action without exiting, so the handler ran, returned, and the loop
# carried on waiting while the caller believed it had stopped.
bash "$SCRIPT" --deadline 60 --interval 1 --until 'false' >/dev/null 2>&1 &
RUNNER=$!
sleep 2
kill -TERM "$RUNNER" 2>/dev/null
sleep 2
check "the runner is gone" "$(kill -0 "$RUNNER" 2>/dev/null && echo alive || echo gone)" "gone"

fi

if want "bad arguments are refused, not defaulted"; then
run --deadline abc --until 'true'
check "a non-numeric deadline"  "$STATUS" "64"
run --deadline 10 --interval 0 --until 'true'
check "a zero interval"         "$STATUS" "64"
run --deadline 10
check "no predicate"            "$STATUS" "64"

fi
echo
echo "  passed: $PASS   failed: $FAIL"
[ "$FAIL" -eq 0 ]
