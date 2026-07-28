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

echo "==> manifest"
store_exists "$BACKUP_ID/manifest.json" || {
    echo "no manifest for $BACKUP_ID: it is not a completed backup" >&2; exit 1; }
store_get "$BACKUP_ID/manifest.json" "$WORK/manifest.json"
EXPECTED_SHA="$(sed -n 's/.*"payload_sha256"[^"]*"\([^"]*\)".*/\1/p' "$WORK/manifest.json")"

echo "==> fetch"
for part in payload.enc payload.mac payload.key; do
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

echo "==> restore into $TARGET"
pg_tool_stdin psql -U "$PGUSER" -d postgres -tAc "DROP DATABASE IF EXISTS \"$TARGET\"" >/dev/null
pg_tool_stdin psql -U "$PGUSER" -d postgres -tAc "CREATE DATABASE \"$TARGET\"" >/dev/null
pg_tool_stdin pg_restore -U "$PGUSER" -d "$TARGET" --no-owner < "$WORK/dump" >/dev/null

echo "restored $BACKUP_ID into $TARGET"
