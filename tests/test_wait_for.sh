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

# Elapsed seconds of a run, alongside its exit code.
run() {
    local start end
    start="$(date +%s)"
    bash "$SCRIPT" "$@" >"$WORK/out" 2>&1
    STATUS=$?
    end="$(date +%s)"
    ELAPSED=$((end - start))
}

echo "== a condition already true returns at once =="
run --deadline 60 --interval 1 --until 'true'
check "exit status is 0"        "$STATUS" "0"
check "it did not sleep first"  "$( [ "$ELAPSED" -le 2 ] && echo yes || echo no )" "yes"

echo "== a condition that becomes true is waited for =="
( sleep 3; : > "$WORK/appeared" ) &
run --deadline 60 --interval 1 --until "test -e $WORK/appeared"
check "exit status is 0"        "$STATUS" "0"
check "it actually waited"      "$( [ "$ELAPSED" -ge 2 ] && echo yes || echo no )" "yes"

echo "== a deadline is required =="
run --interval 1 --until 'true'
check "usage error"             "$STATUS" "64"
check "the reason is named"     "$(grep -qc 'deadline is required' "$WORK/out" && echo yes || echo no)" "yes"

echo "== a deadline that passes is its own outcome =="
run --deadline 3 --interval 1 --until 'false'
check "exit status is 75"       "$STATUS" "75"
# 75, not 1. A caller that cannot tell a timeout from a permanent failure either
# retries what will never succeed or gives up on what only needed longer.
check "within its own budget"   "$( [ "$ELAPSED" -le 6 ] && echo yes || echo no )" "yes"
check "it reports the deadline" "$(grep -qc 'deadline of 3s' "$WORK/out" && echo yes || echo no)" "yes"

echo "== the interval never overshoots the deadline =="
# 3s budget, 30s interval: without the clamp this sleeps 30s past its own budget,
# and an outer timeout kills it before it can say why it stopped.
run --deadline 3 --interval 30 --until 'false'
check "exit status is 75"       "$STATUS" "75"
check "stopped at the deadline" "$( [ "$ELAPSED" -le 6 ] && echo yes || echo no )" "yes"

echo "== a subject that exits ends the wait =="
# The 13h39m loop: the run behind it had been killed by its own watchdog, and the
# wait carried on polling a file that could never appear.
sleep 2 & SUBJECT=$!
run --deadline 60 --interval 1 --pid "$SUBJECT" --until 'false'
check "exit status is 2"        "$STATUS" "2"
check "not held to the deadline" "$( [ "$ELAPSED" -le 8 ] && echo yes || echo no )" "yes"
check "it names the subject"    "$(grep -qc "subject $SUBJECT exited" "$WORK/out" && echo yes || echo no)" "yes"

echo "== a wait can be cancelled =="
# The answer arriving by another route: five loops were left polling results
# already obtained and already used.
( sleep 2; : > "$WORK/stop" ) &
run --deadline 60 --interval 1 --cancel "$WORK/stop" --until 'false'
check "exit status is 3"        "$STATUS" "3"
check "not held to the deadline" "$( [ "$ELAPSED" -le 8 ] && echo yes || echo no )" "yes"

echo "== a predicate that can never be true is not special-cased =="
# A broken predicate (this session: a jq expression with unbalanced quotes) is
# indistinguishable from one that is merely false, so the deadline is what has to
# catch it -- which is why the deadline is mandatory.
run --deadline 3 --interval 1 --until 'this-command-does-not-exist'
check "exit status is 75"       "$STATUS" "75"

echo "== nothing is left running =="
BEFORE="$(pgrep -P $$ 2>/dev/null | wc -l | tr -d ' ')"
run --deadline 3 --interval 1 --until 'false'
sleep 1
AFTER="$(pgrep -P $$ 2>/dev/null | wc -l | tr -d ' ')"
check "no children survive the run" "$AFTER" "$BEFORE"

echo "== a signal stops it, rather than being tidied up and ignored =="
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

echo "== bad arguments are refused, not defaulted =="
run --deadline abc --until 'true'
check "a non-numeric deadline"  "$STATUS" "64"
run --deadline 10 --interval 0 --until 'true'
check "a zero interval"         "$STATUS" "64"
run --deadline 10
check "no predicate"            "$STATUS" "64"

echo
echo "  passed: $PASS   failed: $FAIL"
[ "$FAIL" -eq 0 ]
