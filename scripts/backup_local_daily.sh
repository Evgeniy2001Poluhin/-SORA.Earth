#!/usr/bin/env bash
# A daily local dump. Operational copy, not disaster recovery.
#
# This does NOT protect against losing the server: the dump lands on the same
# disk as the database it came from. It moves RPO from *undefined* to 24 hours
# while the host is alive, which is the difference between not knowing what would
# be lost and knowing. Off-site, encrypted, retained backups are scripts/backup_run.sh
# and need a keypair and a bucket that this host does not have.
#
# Nothing here is claimed until it is verified. A dump that cannot be listed is
# not a backup, so it is checked before it is allowed to replace anything, and
# old copies are removed only after a new one has passed.
set -euo pipefail
umask 077

COMPOSE_FILE="${COMPOSE_FILE:-/opt/sora_earth_ai_platform/docker-compose.prod.yml}"
PROJECT_DIR="${PROJECT_DIR:-/opt/sora_earth_ai_platform}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/sora}"
DB_NAME="${DB_NAME:-sora_earth}"
DB_USER="${DB_USER:-sora}"
KEEP_DAYS="${KEEP_DAYS:-7}"
LOCK_FILE="${LOCK_FILE:-/run/sora-backup-local.lock}"

log()  { logger -t sora-backup -p daemon.info  -- "$*"; echo "$*"; }
fail() { logger -t sora-backup -p daemon.err   -- "FAILED: $*"; echo "FAILED: $*" >&2; exit 1; }

# One at a time. Two dumps of the same database cost twice the IO and produce
# nothing extra, and a slow one overlapping the next would leave two partial
# files racing for the same name.
exec 9>"$LOCK_FILE" || fail "cannot open lock $LOCK_FILE"

# -E 75 so contention is distinguishable from a broken lock. Without it every
# nonzero status reads as "someone else is running" -- a missing directory, a bad
# descriptor or a permissions fault all become a silent skip, and a schedule that
# has not produced a backup for weeks looks healthy. scripts/backup_lock.sh says
# exactly this about the same trap; this file did not apply it.
lock_rc=0
flock -n -E 75 9 || lock_rc=$?
case $lock_rc in
    0)  ;;
    75) log "another run holds the lock; skipping"; exit 0 ;;
    *)  fail "flock failed with status $lock_rc" ;;
esac

mkdir -p "$BACKUP_DIR"
chmod 0700 "$BACKUP_DIR"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FINAL="$BACKUP_DIR/${DB_NAME}_${STAMP}.dump"
TMP="$FINAL.tmp"

cleanup() { rm -f "$TMP"; }
trap cleanup EXIT

cd "$PROJECT_DIR"

# Custom format: pg_restore can read it selectively and verify it without
# restoring. --no-owner and --no-acl because roles are cluster-level and absent
# from a single-database dump; restoring ownership would need those roles to
# exist and can require superuser.
#
# No password anywhere. pg_dump runs inside the container as its own user, so
# nothing is passed on a command line where every process on the host could read
# it from /proc/<pid>/cmdline.
# Dumped and verified inside the container, then copied out.
#
# The first version piped the dump to the host and verified it with
# `pg_restore --list /dev/stdin`. That fails by construction: a custom-format
# archive has to be seeked, and stdin through `docker exec` is a pipe. It failed
# loudly and wrote nothing, which is the design working -- but the verification
# has to happen where the file is a file.
#
# Neither pg_dump nor pg_restore exists on this host, so there is nowhere else
# for it to happen.
CONTAINER_TMP="/tmp/sora_backup_$$.dump"
DC=(docker compose -f "$COMPOSE_FILE" exec -T postgres)

container_cleanup() { "${DC[@]}" rm -f "$CONTAINER_TMP" >/dev/null 2>&1 || true; }
trap 'cleanup; container_cleanup' EXIT

# Wait for PostgreSQL, bounded. After=docker.service waits for the daemon, not
# for the compose service, so a Persistent=true catch-up at boot can start while
# postgres is still coming up. The dump would fail and nothing would retry until
# tomorrow -- a whole day lost to a race of a few seconds.
READY=0
for _ in $(seq 1 30); do
    if "${DC[@]}" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
        READY=1; break
    fi
    sleep 2
done
[ "$READY" = 1 ] || fail "PostgreSQL not ready after 60s"

log "starting dump of $DB_NAME"
"${DC[@]}" pg_dump -U "$DB_USER" -d "$DB_NAME" \
    --format=custom --no-owner --no-acl --file "$CONTAINER_TMP" \
    || fail "pg_dump exited non-zero"

# A file that exists is not a backup. pg_restore --list reads the archive's
# table of contents and fails on a truncated or corrupt dump, without touching
# any database.
"${DC[@]}" pg_restore --list "$CONTAINER_TMP" > /dev/null 2>&1 \
    || fail "pg_restore --list rejected the dump"

"${DC[@]}" cat "$CONTAINER_TMP" > "$TMP" || fail "could not copy the dump out"
[ -s "$TMP" ] || fail "dump is empty after copying out"

# The copy out goes through a pipe, so its integrity is confirmed against the
# size the container reports -- a truncated transfer would otherwise produce a
# smaller file that still lists correctly on the other side.
CONTAINER_SIZE="$("${DC[@]}" stat -c %s "$CONTAINER_TMP" | tr -d '\r')"
HOST_SIZE="$(wc -c < "$TMP" | tr -d ' ')"
[ "$CONTAINER_SIZE" = "$HOST_SIZE" ] \
    || fail "copied $HOST_SIZE bytes, container reported $CONTAINER_SIZE"

SIZE="$(wc -c < "$TMP" | tr -d ' ')"
SHA="$(sha256sum "$TMP" | awk '{print $1}')"

# Atomic, and only now. Until this line no file in the directory could be
# mistaken for a finished backup.
mv "$TMP" "$FINAL"
echo "$SHA  $(basename "$FINAL")" > "$FINAL.sha256"
log "wrote $(basename "$FINAL") — $SIZE bytes — sha256 ${SHA:0:16}"

# Retention runs only here, after a new dump has been written and verified.
# Pruning first would leave a window in which the old copies are gone and the new
# one has not been proven.
DELETED=0
while IFS= read -r old; do
    rm -f "$old" "$old.sha256"
    DELETED=$((DELETED + 1))
done < <(find "$BACKUP_DIR" -name "${DB_NAME}_*.dump" -mtime "+$KEEP_DAYS" -print)
[ "$DELETED" -eq 0 ] || log "pruned $DELETED dump(s) older than $KEEP_DAYS days"

REMAINING="$(find "$BACKUP_DIR" -name "${DB_NAME}_*.dump" | wc -l | tr -d ' ')"
log "done — $REMAINING dump(s) held"
