#!/usr/bin/env bash
# Thin wrapper. The implementation is tools/wait_for.py.
#
# The shell version lived here for six defects, every one in process control:
# a deadline that did not bound the predicate, a kill that missed a grandchild,
# a sweep skipped when the leader died first, a fail-open `ps` reading, a signal
# arriving before the group id existed, and a SIGCONT that would have let a
# process start on the path where it must not.
#
# Bash has no primitive for "give this child its own process group, atomically,
# before it executes anything". `set -m` is a platform property that has to be
# measured, and measuring it afterwards cannot tell a fast predicate from a
# failed measurement. Python's start_new_session=True is an operating-system
# guarantee, so three of those six defects stop existing rather than being
# fixed.
#
# The path stays the same so existing callers do not change.
set -uo pipefail

command -v python3 >/dev/null 2>&1 || {
    echo "wait_for: python3 is required" >&2
    exit 71   # EX_OSERR
}

exec python3 "$(cd "$(dirname "$0")" && pwd)/wait_for.py" "$@"
