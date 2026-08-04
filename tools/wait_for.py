#!/usr/bin/env python3
"""Wait for a condition, with an owner, a deadline that is real, and nothing
left running afterwards.

Every long wait in this repository has been written ad hoc: a `while` loop, a
`sleep`, no deadline, no owner. In one session that produced six loops still
polling results already obtained -- 13h39m, 7h32m, 7h22m, 1h13m, 48m, 36m --
one of them waiting on a process its own watchdog had killed hours earlier.

## Why this is not the shell version

It was, for three days and six defects, every one of them in process control:
the deadline did not bound the predicate; the kill did not reach a grandchild;
the sweep was skipped when the leader died first; an empty `ps` was fail-open;
a signal arriving before the group id was known found nothing to stop; and the
SIGCONT needed to deliver TERM to a stopped process would have let that process
start running on the path where it must not.

That is not six oversights. Bash has no primitive for "give this child its own
process group, atomically, before it executes anything" -- `set -m` is a
platform property that has to be measured, and measuring it after the fact
cannot distinguish a fast predicate from a failed measurement. Three of those
six defects do not exist here:

  start_new_session=True  the group is created in the child before exec. It is
                          an operating-system guarantee, not something to probe
                          for, so there is no handshake and no window between
                          the fork and knowing the group id.
  time.monotonic()        a deadline that a clock adjustment cannot move.
  a flag in the handler   signals are recorded and acted on at a known point,
                          never in the middle of a partially built state.

## What is still not guaranteed

A signal can arrive during the spawn itself. The handler records it, and it is
acted on as soon as Popen returns -- at which point the whole group is stopped.
So the honest contract is: **once the spawn begins the predicate may run
briefly; a signal received during it is handled immediately afterwards, and the
group is then stopped.** Not "the predicate never executed an instruction".
Guaranteeing that needs a gate pipe, and for a wait predicate -- a check the
caller wrote, expected to be side-effect free -- that is not worth its cost.

The sweep reaches what stays in the predicate's process group. A descendant
that calls setsid, daemonises, or is re-parented into another session is
outside what any group signal can reach, and no claim is made about it.

The deadline bounds the *wait*. Stopping what the wait started may add up to
GRACE_SECONDS on top, because a process that ignores TERM is given a bounded
chance to exit before KILL. `--deadline 3` means "stop waiting after 3
seconds", not "returns within 3 seconds".

## Exit codes

A wait that returns "failed" for a timeout, a dead subject and a cancellation
alike forces the caller to guess, and the guess becomes a retry around a
permanent failure.

    0    the predicate became true
    2    the subject exited before it did       (--pid)
    3    cancelled                              (--cancel)
    64   usage
    71   the environment cannot support the guarantee  (EX_OSERR)
    75   the deadline passed  (EX_TEMPFAIL: retryable, unlike the rest)
    130  interrupted   (128 + SIGINT)
    143  terminated    (128 + SIGTERM)
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time

# How often the deadline, the cancellation and the subject are re-examined while
# the predicate runs. Not --interval, which is the gap between attempts: a
# 5-minute interval must not mean a 5-minute overshoot on a 10-second deadline.
POLL = 0.1
# Between TERM and KILL. Bounded on purpose: a predicate that ignores TERM must
# not turn "the deadline passed" into another wait without one.
GRACE_SECONDS = 5.0

EX_USAGE = 64
EX_OSERR = 71
EX_TEMPFAIL = 75

SUBJECT_GONE = 2
CANCELLED = 3

pending_exit: int | None = None


def _on_signal(signum, _frame):
    """Record it. Acting here would run cleanup against whatever state the main
    line happens to be halfway through building."""
    global pending_exit
    pending_exit = 128 + signum


class Usage(argparse.ArgumentParser):
    def error(self, message):
        # argparse exits 2 by default, which is this tool's "the subject
        # exited". A caller distinguishing outcomes by code would read a typo in
        # the arguments as a fact about the process it was waiting on.
        self.exit(EX_USAGE, "wait_for: %s\n" % message)


def parse_args(argv):
    p = Usage(prog="wait_for", description=__doc__)
    p.add_argument("--until", required=True, metavar="COMMAND")
    # Deliberately required and with no default. A default deadline is one
    # somebody chose for a different wait, and every abandoned loop this
    # replaces was written by someone who would have accepted whatever it was.
    #
    # Checked by hand rather than with required=True so the message says what is
    # wrong and why, instead of argparse's "the following arguments are
    # required" -- which names the flag but not the reason it has no default.
    p.add_argument("--deadline", type=int, metavar="SECONDS")
    p.add_argument("--interval", type=int, default=5, metavar="SECONDS")
    p.add_argument("--pid", type=int, metavar="PID")
    p.add_argument("--cancel", metavar="PATH")
    p.add_argument("--label", metavar="TEXT")
    args = p.parse_args(argv)
    if args.deadline is None:
        p.error("--deadline is required; a wait with no end is the defect this exists to prevent")
    if args.deadline < 0:
        p.error("--deadline must not be negative: %d" % args.deadline)
    if args.interval <= 0:
        p.error("--interval must be above zero: %d" % args.interval)
    return args


def group_exists(pgid: int) -> bool:
    """Whether anything is still in the group -- not whether the leader lives.

    The leader can be reaped while a child of it runs on, which is the defect
    that let a satisfied wait hand back a running process.
    """
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Something is there; it is simply not ours to signal.
        return True
    return True


def stop_group(proc, pgid, graceful: bool) -> None:
    """TERM, a bounded grace, KILL, then reap.

    `graceful=False` is for the paths where the predicate was never meant to
    have run: KILL only, no TERM and no SIGCONT. SIGCONT would be needed to
    deliver TERM to a stopped process, and it is exactly what must not be sent
    there -- it would let the process start. SIGKILL needs neither.
    """
    if pgid is None:
        return

    # One place that signals the group, so "the group, not the leader" is a
    # single decision rather than repeated at each call. Mutating one of two
    # copies proved nothing: TERM to the group already collected the tree, so
    # replacing only the KILL left every test green.
    def signal_group(sig):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    if graceful:
        signal_group(signal.SIGTERM)
        until = time.monotonic() + GRACE_SECONDS
        while time.monotonic() < until:
            # Reap the leader inside the loop. An exited leader stays a zombie
            # until it is waited for, and a zombie is still a process: killpg(0)
            # finds it, group_exists says the group is alive, and the grace
            # period runs its full length every single time. Measured: 6s to
            # stop a predicate that died instantly.
            if proc is not None:
                proc.poll()
            if not group_exists(pgid):
                break
            time.sleep(POLL)

    signal_group(signal.SIGKILL)

    if proc is not None:
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def main(argv=None) -> int:
    if os.name != "posix":
        print("wait_for: process-group cleanup needs a POSIX system", file=sys.stderr)
        return EX_OSERR

    args = parse_args(sys.argv[1:] if argv is None else argv)
    label = args.label or args.until

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    started = time.monotonic()
    ends = started + args.deadline
    checks = 0
    proc = None
    pgid = None

    def report(what: str) -> None:
        print("wait_for: %s after %ds and %d checks — %s"
              % (what, round(time.monotonic() - started), checks, label))

    def guard():
        """The outcome that ends the wait now, or None. Consulted continuously,
        including while the predicate runs -- which is the whole point of
        supervising it rather than calling it and looking at the clock after."""
        if pending_exit is not None:
            return pending_exit
        if args.pid is not None:
            try:
                os.kill(args.pid, 0)
            except ProcessLookupError:
                report("subject %d exited" % args.pid)
                return SUBJECT_GONE
            except PermissionError:
                pass
        if args.cancel and os.path.exists(args.cancel):
            report("cancelled via %s" % args.cancel)
            return CANCELLED
        if time.monotonic() >= ends:
            report("deadline of %ds passed" % args.deadline)
            return EX_TEMPFAIL
        return None

    try:
        while True:
            outcome = guard()
            if outcome is not None:
                return outcome

            checks += 1
            try:
                proc = subprocess.Popen(
                    ["bash", "-c", args.until],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    # The group is created in the child, before exec. No probing,
                    # no handshake, and pgid is valid from here on even after the
                    # leader is reaped.
                    start_new_session=True,
                )
            except OSError as exc:
                print("wait_for: cannot start the predicate: %s" % exc, file=sys.stderr)
                return EX_OSERR
            pgid = proc.pid

            # A signal during the spawn is acted on here, at the first point
            # where there is a group to stop.
            while proc.poll() is None:
                outcome = guard()
                if outcome is not None:
                    stop_group(proc, pgid, graceful=True)
                    return outcome
                time.sleep(POLL)

            status = proc.returncode
            # Swept on every path, including success: a predicate can return 0
            # and still have left something running. And before the next
            # attempt, or the new group id replaces the old one and the previous
            # attempt's children become unreachable.
            stop_group(proc, pgid, graceful=True)
            proc, pgid = None, None

            if status == 0:
                report("satisfied")
                return 0

            resume = time.monotonic() + args.interval
            while time.monotonic() < resume:
                outcome = guard()
                if outcome is not None:
                    return outcome
                time.sleep(POLL)
    finally:
        # Whatever the exit -- an outcome, a signal, an exception -- the group
        # does not outlive it.
        stop_group(proc, pgid, graceful=True)


if __name__ == "__main__":
    raise SystemExit(main())
