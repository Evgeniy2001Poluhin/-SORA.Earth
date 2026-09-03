#!/usr/bin/env bash
# Fail when the newest local dump is too old, or when there is none at all.
#
# `scripts/backup_run.sh` alerts when a backup *fails*. Nothing alerts when a
# backup stops *happening*: a disabled timer, a unit that was never enabled
# after a rebuild, a host restored without its schedule. That failure is silent
# by construction -- no job runs, so no job reports -- and it is the one that
# ends with an empty restore. GAP-007 stood at PARTIAL for six weeks on exactly
# this shape: the scripts were merged, tested, documented, and nothing ran them.
#
# ## An empty directory must fail, not pass
#
# "the newest dump is older than N hours" evaluated over zero dumps has no
# newest, and the obvious spelling -- take the first line of `ls -t`, compare its
# mtime -- compares an empty string and quietly succeeds. The count is checked
# before the age, and the count is printed either way, because a threshold
# report without its denominator is how "0 errors" gets read as healthy.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/sora}"
DB_NAME="${DB_NAME:-sora_earth}"
# 26 rather than 24: the schedule is daily, and a run that starts late or takes
# an hour must not page anybody. Two hours of slack, and no more, so a schedule
# that has genuinely stopped is caught on the next check rather than the next week.
MAX_AGE_HOURS="${MAX_AGE_HOURS:-26}"
# Called as: hook <event> <detail>. Same shape as backup_run.sh's, so one hook
# can serve both.
BACKUP_ALERT_HOOK="${BACKUP_ALERT_HOOK:-}"
BACKUP_ALERT_TIMEOUT="${BACKUP_ALERT_TIMEOUT:-30}"

log()  { logger -t sora-backup-age -p daemon.info -- "$*" 2>/dev/null || true; echo "$*"; }
warn() { logger -t sora-backup-age -p daemon.err  -- "$*" 2>/dev/null || true; echo "$*" >&2; }

alert() {
    [ -n "$BACKUP_ALERT_HOOK" ] || return 0
    # Never let a broken hook turn a real finding into a crash: the finding is
    # already logged and the exit code already decided by the time this runs.
    "$BACKUP_ALERT_HOOK" "$1" "$2" </dev/null >/dev/null 2>&1 || \
        warn "alert hook failed; the finding above still stands"
}

if [ ! -d "$BACKUP_DIR" ]; then
    warn "STALE: $BACKUP_DIR does not exist, so no backup has ever been written here"
    alert backup_missing "$BACKUP_DIR does not exist"
    exit 1
fi

# `find`, not `ls`, so a directory with no matches yields nothing rather than an
# error message that a later pipe would treat as a filename.
#
# A while-read loop rather than `mapfile`, which needs bash 4 -- macOS ships 3.2,
# and tests/test_backup_automation.py rejects the newer builtins so these scripts
# stay runnable where they are edited as well as where they run.
count=0
newest=""
newest_epoch=0
while IFS= read -r f; do
    count=$((count + 1))
    # GNU stat here; the script runs on the host, which is Linux.
    epoch=$(stat -c %Y "$f" 2>/dev/null || echo 0)
    if [ "$epoch" -gt "$newest_epoch" ]; then
        newest_epoch=$epoch
        newest=$f
    fi
done < <(find "$BACKUP_DIR" -maxdepth 1 -name "${DB_NAME}_*.dump" -type f 2>/dev/null)

if [ "$count" -eq 0 ]; then
    warn "STALE: no ${DB_NAME}_*.dump in $BACKUP_DIR (0 files examined)"
    alert backup_missing "no dumps in $BACKUP_DIR"
    exit 1
fi

age_hours=$(( ( $(date +%s) - newest_epoch ) / 3600 ))

if [ "$age_hours" -gt "$MAX_AGE_HOURS" ]; then
    warn "STALE: newest of $count dump(s) is ${age_hours}h old (limit ${MAX_AGE_HOURS}h): $(basename "$newest")"
    alert backup_stale "${age_hours}h old, limit ${MAX_AGE_HOURS}h"
    exit 1
fi

log "ok: newest of $count dump(s) is ${age_hours}h old (limit ${MAX_AGE_HOURS}h)"
