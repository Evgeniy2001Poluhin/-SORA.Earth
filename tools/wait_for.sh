#!/usr/bin/env bash
# Wait for a condition, with an owner, a deadline, and nothing left behind.
#
# Every long wait in this repository has been written ad hoc: a `while` loop, a
# `sleep`, no deadline, no owner. That has produced, in one session alone, six
# loops still polling results that had already been obtained -- 13h39m, 7h32m,
# 7h22m, 1h13m, 48m, 36m -- one of them waiting on a process its own watchdog had
# killed hours earlier. And two subtler failures that a loop cannot notice:
#
#   * a predicate with a syntax error, which can never be true, burning to its
#     deadline while looking like work in progress;
#   * a wait started for one commit that silently followed the branch to the
#     next, so "green on <sha>" described a commit nobody waited for.
#
# Both are answered here by making the wait state its subject and check it.
#
#   tools/wait_for.sh --deadline 600 --until 'test -f /tmp/done'
#   tools/wait_for.sh --deadline 900 --pid "$BUILD_PID" --until '...'
#   tools/wait_for.sh --deadline 300 --cancel /tmp/stop --until '...'
#
# Exit codes, so a caller can tell the outcomes apart. A wait that returns
# "failed" for a timeout, a dead subject and a cancellation alike forces the
# caller to guess, and the guess becomes a retry loop around a permanent
# failure:
#
#   0   the predicate became true
#   2   the subject exited before it did       (--pid)
#   3   cancelled                              (--cancel)
#   64  usage
#   75  the deadline passed  (EX_TEMPFAIL: retryable, unlike the rest)
set -uo pipefail

UNTIL="" ; PID="" ; DEADLINE="" ; INTERVAL=5 ; CANCEL="" ; LABEL=""

die() { printf 'wait_for: %s\n' "$1" >&2; exit 64; }

while [ $# -gt 0 ]; do
    case "$1" in
        --until)    UNTIL="${2-}"    ; shift 2 || die "--until needs a command" ;;
        --pid)      PID="${2-}"      ; shift 2 || die "--pid needs a number" ;;
        --deadline) DEADLINE="${2-}" ; shift 2 || die "--deadline needs seconds" ;;
        --interval) INTERVAL="${2-}" ; shift 2 || die "--interval needs seconds" ;;
        --cancel)   CANCEL="${2-}"   ; shift 2 || die "--cancel needs a path" ;;
        --label)    LABEL="${2-}"    ; shift 2 || die "--label needs text" ;;
        *) die "unknown argument: $1" ;;
    esac
done

[ -n "$UNTIL" ] || die "--until is required"
# Deliberately not optional and with no default. A default deadline is one
# somebody chose for a different wait, and the loops this replaces were all
# written by someone who would have accepted whatever it was.
[ -n "$DEADLINE" ] || die "--deadline is required; a wait with no end is the defect this exists to prevent"
case "$DEADLINE" in ''|*[!0-9]*) die "--deadline must be whole seconds: $DEADLINE" ;; esac
case "$INTERVAL" in ''|*[!0-9]*) die "--interval must be whole seconds: $INTERVAL" ;; esac
[ "$INTERVAL" -gt 0 ] || die "--interval must be above zero"
if [ -n "$PID" ]; then
    case "$PID" in ''|*[!0-9]*) die "--pid must be a number: $PID" ;; esac
fi

LABEL="${LABEL:-$UNTIL}"

# Every exit path, including the interrupt. A wait that leaves a child behind has
# reproduced the thing it was written to stop.
cleanup() {
    local kids
    kids="$(pgrep -P $$ 2>/dev/null | tr '\n' ' ')"
    if [ -n "${kids// /}" ]; then
        # shellcheck disable=SC2086
        #   Deliberately unquoted: $kids is a list of PIDs to be split.
        kill $kids 2>/dev/null
    fi
}
trap cleanup EXIT
# Separately, and each with an exit. `trap cleanup INT` replaces the default
# action -- which is to terminate -- so the handler ran, returned, and the loop
# carried on waiting. An interrupt that tidies up and then keeps going is worse
# than no handler at all: the caller believes it stopped.
trap 'exit 130' INT    # 128 + SIGINT
trap 'exit 143' TERM   # 128 + SIGTERM

started="$(date +%s)"
ends=$((started + DEADLINE))
checks=0

report() {
    printf 'wait_for: %s after %ds and %d checks — %s\n' \
        "$1" "$(( $(date +%s) - started ))" "$checks" "$LABEL"
}

while :; do
    # The subject first. A predicate that can never become true because the
    # process behind it is gone would otherwise run to the deadline, and the
    # deadline is the least informative way to learn that.
    if [ -n "$PID" ] && ! kill -0 "$PID" 2>/dev/null; then
        report "subject $PID exited"
        exit 2
    fi

    if [ -n "$CANCEL" ] && [ -e "$CANCEL" ]; then
        report "cancelled via $CANCEL"
        exit 3
    fi

    checks=$((checks + 1))
    if bash -c "$UNTIL" >/dev/null 2>&1; then
        report "satisfied"
        exit 0
    fi

    now="$(date +%s)"
    [ "$now" -lt "$ends" ] || { report "deadline of ${DEADLINE}s passed"; exit 75; }

    # Never sleep past the deadline: the last wait of a long interval is what
    # turns a 600s budget into 660s, and a caller timing this out from outside
    # kills it without the diagnostic above.
    remaining=$((ends - now))
    if [ "$remaining" -lt "$INTERVAL" ]; then sleep "$remaining"; else sleep "$INTERVAL"; fi
done
