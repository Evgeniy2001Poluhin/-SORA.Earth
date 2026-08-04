#!/usr/bin/env bash
# Wait for a condition, with an owner, a deadline that is real, and nothing left
# running afterwards.
#
# Every long wait in this repository has been written ad hoc: a `while` loop, a
# `sleep`, no deadline, no owner. In one session that produced six loops still
# polling results already obtained -- 13h39m, 7h32m, 7h22m, 1h13m, 48m, 36m --
# one of them waiting on a process its own watchdog had killed hours earlier.
# And two failures a loop cannot notice: a predicate with a syntax error, which
# can never be true, burning to its deadline while looking like work; and a wait
# started for one commit that silently followed the branch to the next.
#
#   tools/wait_for.sh --deadline 600 --until 'test -f /tmp/done'
#   tools/wait_for.sh --deadline 900 --pid "$BUILD_PID" --until '...'
#   tools/wait_for.sh --deadline 300 --cancel /tmp/stop --until '...'
#
# ## The deadline bounds the wait, not the polling interval
#
# The first version ran the predicate in the foreground and checked the clock
# after it returned. `--deadline 3 --until 'sleep 20; false'` therefore took 20
# seconds and then reported the deadline as though it had been enforced. So did
# the cancel file and the subject check: nothing was looked at while the
# predicate held the loop. A tool that promises a deadline and does not bound
# the wait is worse than none, because it is believed.
#
# Review found it; the suite did not. Its "a predicate that can never be true"
# case used a command that fails instantly -- the one shape where the defect
# cannot appear.
#
# The predicate now runs in its own process group and the loop supervises it, so
# the deadline, the cancellation and the subject stay live throughout.
#
# ## What the cleanup does and does not cover
#
# Everything left in the predicate's process group is stopped. A descendant that
# leaves that group -- calls setsid, daemonises, is re-parented into another
# session -- is outside what any group signal can reach, and this makes no claim
# about it. The predicate is a check the caller wrote, and is expected to behave
# like one.
#
# The deadline bounds the *wait*. Stopping what the wait started may add up to
# GRACE_TICKS * POLL on top -- five seconds as configured -- because a process
# that ignores TERM is given a bounded chance to exit before KILL. So
# `--deadline 3` means "stop waiting after 3 seconds", not "this command returns
# within 3 seconds": the tests allow 8 for exactly this reason.
#
# Exit codes, so a caller can tell the outcomes apart. A wait that returns
# "failed" for a timeout, a dead subject and a cancellation alike forces the
# caller to guess, and the guess becomes a retry around a permanent failure:
#
#   0   the predicate became true
#   2   the subject exited before it did       (--pid)
#   3   cancelled                              (--cancel)
#   64  usage
#   71  the predicate got no private process group (EX_OSERR: the environment)
#   75  the deadline passed  (EX_TEMPFAIL: retryable, unlike the rest)
set -uo pipefail

UNTIL="" ; PID="" ; DEADLINE="" ; INTERVAL=5 ; CANCEL="" ; LABEL=""

# How often the deadline, the cancellation and the subject are re-examined while
# the predicate runs. Not --interval, which is the gap between attempts: a
# 5-minute interval must not mean a 5-minute overshoot on a 10-second deadline.
POLL=0.25
# Grace between TERM and KILL, in POLL-sized steps. Bounded on purpose: a
# predicate that ignores TERM must not turn "the deadline passed" into another
# wait without one.
GRACE_TICKS=20

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
started="$(date +%s)"
ends=$((started + DEADLINE))
checks=0
predicate_pid=""
# Kept separately, and deliberately not derived from the leader's liveness. A
# predicate that backgrounds something and exits -- `sh -c 'work & exit 0'` --
# leaves a group that outlives its leader, and a function keyed on the leader
# returns having sent nothing to it. Measured before this was split: the waiter
# reported success in 0s and the child was still running.
predicate_pgid=""
predicate_status=1

start_predicate() {
    # Its own process group, so the whole tree can be stopped. A predicate that
    # forks -- `curl | jq`, a python child, anything with an `&` in it -- leaves
    # grandchildren that killing the direct child never reaches.
    #
    # `set -m` rather than `setsid`: setsid does not exist on macOS, and job
    # control gives an asynchronous command its own group on both. Verified: the
    # backgrounded command's PGID equals its PID.
    set -m
    bash -c "$UNTIL" >/dev/null 2>&1 &
    predicate_pid=$!
    set +m

    # Measured, not assumed. Everything below depends on the predicate having a
    # private group, and `set -m` giving it one is a property of bash and of the
    # platform -- verified on macOS and on Linux in CI, which is not the same as
    # guaranteed everywhere this may run. Unchecked, a platform where it does not
    # hold would turn "the group is always swept" into a negative kill that
    # quietly matches nothing, and the tool would go back to leaking silently.
    local observed
    observed="$(ps -o pgid= -p "$predicate_pid" 2>/dev/null | tr -d '[:space:]')"
    if [ -n "$observed" ] && [ "$observed" != "$predicate_pid" ]; then
        kill -TERM "$predicate_pid" 2>/dev/null
        wait "$predicate_pid" 2>/dev/null
        predicate_pid=""
        printf 'wait_for: the predicate did not get a private process group (pgid %s, pid %s); refusing to run without one\n' \
            "$observed" "$predicate_pid" >&2
        exit 71   # EX_OSERR: the environment, not the arguments
    fi

    # An empty reading means the leader already exited -- a fast predicate, and
    # the common case. The group id is still its pid: either the group survives
    # with children in it, or there is nothing to sweep and the sweep finds
    # nothing. Both are safe; what is not safe is a live leader in someone
    # else's group, which is what the check above refuses.
    predicate_pgid=$predicate_pid
}

stop_predicate() {
    # Keyed on the group, never on whether the leader is still alive. The
    # version that returned early for a dead leader let this through:
    #
    #   --until "sh -c 'sleep 25 & exit 0'"   → waiter exits 0 in 0s,
    #                                           sleep 25 still running
    #
    # and the tree test did not catch it, because its predicate ends in `wait`
    # -- so the leader stays alive for the whole run and the early return is
    # never taken. A test whose shape excludes the defect proves the opposite of
    # what it claims.
    [ -n "$predicate_pgid" ] || return 0

    kill -TERM -- "-$predicate_pgid" 2>/dev/null

    local ticks=0
    while kill -0 -- "-$predicate_pgid" 2>/dev/null && [ "$ticks" -lt "$GRACE_TICKS" ]; do
        sleep "$POLL"
        ticks=$((ticks + 1))
    done

    kill -KILL -- "-$predicate_pgid" 2>/dev/null
    # Reaped, not merely signalled, when the leader is still ours to reap.
    # Without this the function returns while the child is a zombie, and
    # "nothing is left running" becomes a claim about timing rather than state.
    if [ -n "$predicate_pid" ]; then
        wait "$predicate_pid" 2>/dev/null
    fi
    predicate_pid=""
    predicate_pgid=""
}

cleanup() {
    stop_predicate
    local kids
    kids="$(pgrep -P $$ 2>/dev/null | tr '\n' ' ')"
    if [ -n "${kids// /}" ]; then
        # shellcheck disable=SC2086
        #   Deliberately unquoted: $kids is a list of PIDs to be split.
        kill $kids 2>/dev/null
        wait 2>/dev/null
    fi
}
trap cleanup EXIT
# Separately, and each with an exit. `trap cleanup INT TERM` replaces the
# default action -- which is to terminate -- so the handler ran, returned, and
# the loop carried on waiting while the caller believed it had stopped.
trap 'exit 130' INT    # 128 + SIGINT
trap 'exit 143' TERM   # 128 + SIGTERM

report() {
    printf 'wait_for: %s after %ds and %d checks — %s\n' \
        "$1" "$(( $(date +%s) - started ))" "$checks" "$LABEL"
}

# Checked continuously, including while the predicate is running -- which is the
# whole point of supervising it rather than calling it in the foreground.
guard() {
    if [ -n "$PID" ] && ! kill -0 "$PID" 2>/dev/null; then
        stop_predicate; report "subject $PID exited"; exit 2
    fi
    if [ -n "$CANCEL" ] && [ -e "$CANCEL" ]; then
        stop_predicate; report "cancelled via $CANCEL"; exit 3
    fi
    if [ "$(date +%s)" -ge "$ends" ]; then
        stop_predicate; report "deadline of ${DEADLINE}s passed"; exit 75
    fi
}

while :; do
    guard
    checks=$((checks + 1))
    start_predicate

    while kill -0 "$predicate_pid" 2>/dev/null; do
        guard
        sleep "$POLL"
    done
    wait "$predicate_pid" 2>/dev/null
    predicate_status=$?
    predicate_pid=""

    # The status is taken first and the group swept second, in that order. The
    # sweep must happen on the successful path too: a predicate can return 0 and
    # still have left something running, which is how a "satisfied" wait used to
    # hand back a child nobody was going to stop. And it must happen before the
    # next attempt, or the new group id overwrites the old one and the previous
    # attempt's leftovers become unreachable.
    stop_predicate

    [ "$predicate_status" -eq 0 ] && { report "satisfied"; exit 0; }

    # The gap between attempts, guarded the same way: an interval longer than
    # the remaining budget must not delay the verdict past the deadline.
    resume=$(( $(date +%s) + INTERVAL ))
    while [ "$(date +%s)" -lt "$resume" ]; do
        guard
        sleep "$POLL"
    done
done
