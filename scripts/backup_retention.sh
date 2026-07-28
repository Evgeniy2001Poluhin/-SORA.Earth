#!/usr/bin/env bash
# Decide what to keep, then delete the rest -- in that order.
#
#   rolling: the newest N completed backups
#   weekly:  the newest completed backup of each of the last M ISO weeks
#
# One backup can be both; the keep-set is a union, so a weekly pick is never
# removed for falling out of the rolling window.
#
# The whole keep-set is computed before anything is deleted. Deciding and
# deleting in one pass means a listing error partway through can remove
# something the rest of the pass would have kept.
#
# Only completed backups are considered -- a partial upload has no manifest, is
# not a backup, and cannot take the weekly slot. Debris is swept separately
# after a grace period, so an upload still in flight is never mistaken for it.
#
# Written for bash 3.2: no mapfile, no associative arrays. macOS ships 3.2, and
# a maintenance script that only runs on the deployment host is a script whose
# faults are found in production.
#
#   ./scripts/backup_retention.sh            # dry run: prints, deletes nothing
#   BACKUP_RETENTION_APPLY=1 ./scripts/...   # actually deletes
set -euo pipefail

cd "$(dirname "$0")/.."
source scripts/backup_store.sh

KEEP_ROLLING="${BACKUP_KEEP_ROLLING:-28}"
KEEP_WEEKLY="${BACKUP_KEEP_WEEKLY:-8}"
APPLY="${BACKUP_RETENTION_APPLY:-0}"

store_ready

ALL="$(store_list_backups)"
if [ -z "$ALL" ]; then
    echo "no completed backups; nothing to do"
    exit 0
fi

# Ids start with an ISO-8601 UTC stamp, so lexical order is chronological.
NEWEST_FIRST="$(printf '%s\n' "$ALL" | sort -r)"
TOTAL="$(printf '%s\n' "$ALL" | grep -c .)"

week_of() {  # <backup-id> -> ISO year-week
    local stamp="${1%%-*}"
    local day="${stamp:0:4}-${stamp:4:2}-${stamp:6:2}"
    date -u -d "$day" +%G-W%V 2>/dev/null ||
        date -u -j -f %Y-%m-%d "$day" +%G-W%V 2>/dev/null ||
        echo "$day"
}

KEEP=""
add_keep() {  # <id> <reason>
    printf '%s\n' "$KEEP" | grep -qxF "$1" 2>/dev/null && return 0
    KEEP="$(printf '%s\n%s' "$KEEP" "$1")"
    printf '  keep   %s  (%s)\n' "$1" "$2"
}

# rolling
if [ "$KEEP_ROLLING" -gt 0 ]; then
    while IFS= read -r id; do
        [ -n "$id" ] || continue
        add_keep "$id" rolling
    done <<< "$(printf '%s\n' "$NEWEST_FIRST" | head -n "$KEEP_ROLLING")"
fi

# weekly: first sighting of each week, newest first, up to the limit
SEEN_WEEKS=""
WEEKS=0
while IFS= read -r id; do
    [ -n "$id" ] || continue
    [ "$WEEKS" -ge "$KEEP_WEEKLY" ] && break
    week="$(week_of "$id")"
    printf '%s\n' "$SEEN_WEEKS" | grep -qxF "$week" 2>/dev/null && continue
    SEEN_WEEKS="$(printf '%s\n%s' "$SEEN_WEEKS" "$week")"
    WEEKS=$((WEEKS + 1))
    add_keep "$id" "weekly $week"
done <<< "$NEWEST_FIRST"

# Whatever the settings say, the newest completed backup stays. A configuration
# that would empty the store is a configuration mistake, not an instruction.
NEWEST="$(printf '%s\n' "$NEWEST_FIRST" | head -n 1)"
add_keep "$NEWEST" newest

KEPT="$(printf '%s\n' "$KEEP" | grep -c . || true)"
echo "completed backups: $TOTAL   keeping: $KEPT"

FAILURES=0
while IFS= read -r id; do
    [ -n "$id" ] || continue
    if printf '%s\n' "$KEEP" | grep -qxF "$id"; then
        continue
    fi
    if [ "$APPLY" = "1" ]; then
        if store_delete_backup "$id"; then
            echo "  delete $id"
        else
            echo "  FAILED to delete $id" >&2
            FAILURES=$((FAILURES + 1))
        fi
    else
        echo "  would delete $id"
    fi
done <<< "$ALL"

[ "$APPLY" = "1" ] || echo "dry run: set BACKUP_RETENTION_APPLY=1 to act"
[ "$FAILURES" -eq 0 ]
