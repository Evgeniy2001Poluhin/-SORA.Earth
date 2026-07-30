#!/usr/bin/env bash
# Restore one backup into one database, deliberately.
#
#   BACKUP_RESTORE_TARGET=sora_drill ./scripts/backup_restore.sh <backup-id>
#
# Both arguments are explicit and neither has a default. A restore overwrites a
# database; guessing which one is not a thing this script does.
#
# Production is refused unless the operator says so twice -- and even then this
# is not the mechanism for it. A production restore is a decision with a
# rollback plan, not a command.
set -euo pipefail

cd "$(dirname "$0")/.."
source scripts/pg_lib.sh
source scripts/backup_crypt.sh
source scripts/backup_store.sh

BACKUP_ID="${1:-}"
TARGET="${BACKUP_RESTORE_TARGET:-}"
IDENTITY="${BACKUP_IDENTITY_KEY:-}"
PROTECTED="${BACKUP_PROTECTED_DATABASES:-sora_earth,postgres}"

[ -n "$BACKUP_ID" ] || { echo "usage: BACKUP_RESTORE_TARGET=<db> $0 <backup-id>" >&2; exit 2; }
[ -n "$TARGET" ] || { echo "BACKUP_RESTORE_TARGET must name the database to overwrite" >&2; exit 2; }
[ -r "$IDENTITY" ] || { echo "BACKUP_IDENTITY_KEY must point at the private identity" >&2; exit 2; }

IFS=',' read -ra GUARDED <<< "$PROTECTED"
for name in "${GUARDED[@]}"; do
    if [ "$TARGET" = "${name// /}" ]; then
        echo "refusing to restore over '$TARGET': it is a protected database." >&2
        echo "A production restore is an owner decision with its own plan, not this script." >&2
        exit 3
    fi
done

store_ready
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT   # no plaintext dump survives, on any exit path
STAGING=""
drop_staging() { :; }

echo "==> manifest"
store_exists "$BACKUP_ID/manifest.json" || {
    echo "no manifest for $BACKUP_ID: it is not a completed backup" >&2; exit 1; }
store_get "$BACKUP_ID/manifest.json" "$WORK/manifest.json"
EXPECTED_SHA="$(sed -n 's/.*"payload_sha256"[^"]*"\([^"]*\)".*/\1/p' "$WORK/manifest.json")"

echo "==> fetch"
for part in payload.enc payload.hdr payload.mac payload.key; do
    store_get "$BACKUP_ID/$part" "$WORK/$part"
done

echo "==> verify the payload against the manifest"
if command -v sha256sum >/dev/null 2>&1; then
    ACTUAL_SHA="$(sha256sum "$WORK/payload.enc" | awk '{print $1}')"
else
    ACTUAL_SHA="$(shasum -a 256 "$WORK/payload.enc" | awk '{print $1}')"
fi
[ "$ACTUAL_SHA" = "$EXPECTED_SHA" ] || {
    echo "payload checksum does not match the manifest -- refusing" >&2; exit 1; }

echo "==> decrypt and decompress"
backup_decrypt "$WORK/payload" "$IDENTITY" "$WORK/dump.gz"
gunzip -c "$WORK/dump.gz" > "$WORK/dump"

# Restore into a staging database and only then take the name. Two failures are
# avoided by that order, and neither is hypothetical:
#
#   * dropping the target first destroys what was there before the replacement
#     is known to work. A restore that fails halfway would have left nothing to
#     go back to.
#   * a non-zero exit from pg_restore does not undo the statements it already
#     applied. Without a transaction the target is left partially populated --
#     which is worse than empty, because it looks usable.
#
# --single-transaction gives the second guarantee inside the restore, and
# implies --exit-on-error: the first failing statement aborts everything rather
# than being logged and skipped. It rules out parallel restore, which this does
# not use.
STAGING="${TARGET}_restore_$$"
echo "==> restore into staging $STAGING"

drop_staging() {
    pg_tool_stdin psql -U "$PGUSER" -d postgres -tAc \
        "DROP DATABASE IF EXISTS \"$STAGING\"" >/dev/null 2>&1 || true
}
trap 'rm -rf "$WORK"; drop_staging' EXIT

pg_tool_stdin psql -U "$PGUSER" -d postgres -tAc "DROP DATABASE IF EXISTS \"$STAGING\"" >/dev/null
pg_tool_stdin psql -U "$PGUSER" -d postgres -tAc "CREATE DATABASE \"$STAGING\"" >/dev/null

if ! pg_tool_stdin pg_restore -U "$PGUSER" -d "$STAGING" \
        --single-transaction --exit-on-error --no-owner < "$WORK/dump" >/dev/null; then
    echo "restore failed; $TARGET is untouched and the staging copy is discarded" >&2
    exit 1
fi

echo "==> promote staging to $TARGET"
pg_tool_stdin psql -U "$PGUSER" -d postgres -tAc "DROP DATABASE IF EXISTS \"$TARGET\"" >/dev/null
pg_tool_stdin psql -U "$PGUSER" -d postgres -tAc \
    "ALTER DATABASE \"$STAGING\" RENAME TO \"$TARGET\"" >/dev/null
STAGING=""   # promoted; nothing left to discard

echo "restored $BACKUP_ID into $TARGET"
