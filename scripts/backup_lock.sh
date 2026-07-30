#!/usr/bin/env bash
# Mutual exclusion for the backup job, in a directory nobody else can write to.
#
# The previous arrangement kept a lock directory under /tmp and trusted the
# hostname and pid written inside it. Both parts were wrong: any local user can
# create a predictable path in /tmp, and having created it they choose what the
# metadata says. Pre-creating that directory with the pid of some long-lived
# process would have stopped every backup, indefinitely and quietly.
#
# The fix is the *directory*, not the locking primitive. Once the lock lives
# somewhere owned by this user and closed to everyone else, there is nothing to
# pre-create and the metadata inside becomes trustworthy because only we can
# write it.
#
# On top of that, flock when the platform has it: the kernel releases the lock
# when the process dies, so a crash needs no cleanup and no staleness judgement
# at all. macOS ships no flock, so there is a directory-based fallback -- and it
# is announced, never silent. A silent flock fallback is exactly how the earlier
# version reported "another run holds the lock" when the binary was simply
# missing, and a schedule looked healthy while backing up nothing.
#
# No `set -e` here on purpose. This file is sourced, so any option it sets lands
# in the caller's shell -- and errexit there turns "return 75, someone else holds
# it" into an immediate exit, skipping the branch that reports the skip. Callers
# set their own options; a library that overrides them is deciding for code it
# cannot see.

BACKUP_RUNTIME_DIR="${BACKUP_RUNTIME_DIR:-}"
SORA_ENV="${SORA_ENV:-development}"

_stat_owner() {  # portable: GNU stat -c, BSD stat -f
    stat -c '%u' "$1" 2>/dev/null || stat -f '%u' "$1" 2>/dev/null
}
_stat_mode() {
    stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1" 2>/dev/null
}

validate_runtime_dir() {  # <dir>
    local dir="$1" owner mode
    [ -n "$dir" ] || { echo "lock: no runtime directory given" >&2; return 1; }
    # -L before -d: a symlink to a directory satisfies -d, and following it is
    # how a predictable path becomes someone else's directory.
    if [ -L "$dir" ]; then
        echo "lock: $dir is a symlink; refusing to use it" >&2; return 1
    fi
    [ -d "$dir" ] || { echo "lock: $dir does not exist" >&2; return 1; }
    owner="$(_stat_owner "$dir")"
    mode="$(_stat_mode "$dir")"
    if [ "$owner" != "$(id -u)" ]; then
        echo "lock: $dir is owned by uid $owner, not $(id -u); refusing" >&2; return 1
    fi
    # Anything group- or world-writable puts the lock back where it started.
    if [ "$(( 8#$mode & 8#077 ))" -ne 0 ]; then
        echo "lock: $dir has mode $mode; it must not be group- or world-accessible" >&2
        return 1
    fi
    # Deliberately no chmod or chown here. A backup job that repairs its own
    # runtime directory's permissions hides the misconfiguration instead of
    # reporting it, and does so with whatever privilege it happens to hold.
    return 0
}

resolve_runtime_dir() {
    # Explicit first. In production it is the only option: guessing a directory
    # for a scheduled job is how two runs end up with two different locks and no
    # exclusion between them.
    if [ -n "$BACKUP_RUNTIME_DIR" ]; then
        validate_runtime_dir "$BACKUP_RUNTIME_DIR" || return 1
        printf '%s' "$BACKUP_RUNTIME_DIR"; return 0
    fi
    # systemd's RuntimeDirectory=, which it creates as the service user and
    # removes on stop.
    if [ -n "${RUNTIME_DIRECTORY:-}" ] && validate_runtime_dir "${RUNTIME_DIRECTORY%%:*}"; then
        printf '%s' "${RUNTIME_DIRECTORY%%:*}"; return 0
    fi
    if [ "$SORA_ENV" = "production" ]; then
        echo "lock: BACKUP_RUNTIME_DIR is required in production." >&2
        echo "      Set RuntimeDirectory=sora-earth and RuntimeDirectoryMode=0700" >&2
        echo "      in the unit, or point BACKUP_RUNTIME_DIR at a 0700 directory" >&2
        echo "      owned by the service user." >&2
        return 1
    fi
    # Development only, and still validated rather than assumed.
    local candidate="${XDG_RUNTIME_DIR:-}/sora-earth"
    if [ -n "${XDG_RUNTIME_DIR:-}" ]; then
        mkdir -p "$candidate" 2>/dev/null || true
        chmod 700 "$candidate" 2>/dev/null || true
        if validate_runtime_dir "$candidate"; then printf '%s' "$candidate"; return 0; fi
    fi
    candidate="${TMPDIR:-/tmp}/sora-earth-$(id -u)"
    mkdir -p "$candidate" 2>/dev/null || true
    chmod 700 "$candidate" 2>/dev/null || true
    validate_runtime_dir "$candidate" || return 1
    printf '%s' "$candidate"
}

# Its own function so the test suite can exercise the directory backend on a
# platform that has flock. Overriding a shell function requires already being
# inside this process; it is not a switch production can be talked into.
_have_flock() { command -v flock >/dev/null 2>&1; }

# Set by acquire_backup_lock so the caller can report it.
BACKUP_LOCK_BACKEND=""
BACKUP_LOCK_DIR=""

acquire_backup_lock() {  # <name> -> 0 held, 75 someone else holds it, 1 unusable
    local name="$1" runtime lockfile
    runtime="$(resolve_runtime_dir)" || return 1
    BACKUP_LOCK_DIR="$runtime"
    lockfile="$runtime/$name.lock"

    if [ -L "$lockfile" ]; then
        echo "lock: $lockfile is a symlink; refusing" >&2; return 1
    fi
    # The enclosing directory being 0700 and ours already implies this. Checked
    # anyway, because that implication travels through whatever path an operator
    # put in BACKUP_RUNTIME_DIR, and a lock is a bad place to rely on an
    # invariant established somewhere else.
    if [ -e "$lockfile" ]; then
        local lf_owner
        lf_owner="$(_stat_owner "$lockfile")"
        if [ "$lf_owner" != "$(id -u)" ]; then
            echo "lock: $lockfile is owned by uid $lf_owner, not $(id -u); refusing" >&2
            return 1
        fi
    fi

    if _have_flock; then
        BACKUP_LOCK_BACKEND=flock
        # The descriptor stays open for the life of the process. The kernel drops
        # the lock when the process does, however it ends, so there is no stale
        # state to reason about.
        exec 9>"$lockfile" || { echo "lock: cannot open $lockfile" >&2; return 1; }
        if ! flock -n 9; then
            echo "lock: another run holds $lockfile" >&2
            return 75
        fi
        return 0
    fi

    # No flock: say so. This is the fallback, not a silent equivalent.
    BACKUP_LOCK_BACKEND=mkdir
    echo "lock: flock is unavailable on this platform; using a directory lock in" >&2
    echo "      $runtime. Safe because that directory is 0700 and ours, but it" >&2
    echo "      needs staleness handling that flock would not." >&2
    local dir="$lockfile.d"
    if ! mkdir "$dir" 2>/dev/null; then
        local pid start
        pid="$(cat "$dir/pid" 2>/dev/null || echo "")"
        start="$(cat "$dir/start" 2>/dev/null || echo "")"
        # Trustworthy here, and only here: the enclosing directory is 0700 and
        # ours, so nothing else could have written this.
        if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
            echo "lock: reclaiming a lock left by process $pid, which is gone" >&2
            rm -rf "$dir"
            mkdir "$dir" 2>/dev/null || { echo "lock: lost the reclaim race" >&2; return 75; }
        elif [ -n "$pid" ] && [ -n "$start" ] \
             && [ "$(ps -o lstart= -p "$pid" 2>/dev/null | tr -s ' ')" != "$start" ]; then
            echo "lock: pid $pid was reused by a different process; reclaiming" >&2
            rm -rf "$dir"
            mkdir "$dir" 2>/dev/null || { echo "lock: lost the reclaim race" >&2; return 75; }
        else
            echo "lock: another run holds $dir" >&2
            return 75
        fi
    fi
    echo $$ > "$dir/pid"
    ps -o lstart= -p $$ 2>/dev/null | tr -s ' ' > "$dir/start" || true
    BACKUP_LOCK_HELD_DIR="$dir"
    return 0
}

release_backup_lock() {
    # flock needs nothing: the kernel has it. The directory backend does.
    [ "${BACKUP_LOCK_BACKEND:-}" = "mkdir" ] || return 0
    [ -n "${BACKUP_LOCK_HELD_DIR:-}" ] || return 0
    rm -rf "$BACKUP_LOCK_HELD_DIR"
}
