#!/usr/bin/env bash
# One scheduled backup, end to end.
#
#   lock -> dump -> compress -> encrypt -> checksum -> upload -> verify
#        -> publish manifest -> retention -> alert on failure
#
# Every stage fails closed. The manifest is written last and is the only thing
# that makes a backup real: an interrupted run leaves objects behind, but
# nothing that restore or retention will treat as a backup.
#
#   ./scripts/backup_run.sh sora_earth
#
# Nothing here touches production. It reads a database and writes to an object
# store; it is the schedule that decides which database, and installing that
# schedule is a separate, deliberate act.
set -euo pipefail

cd "$(dirname "$0")/.."
source scripts/pg_lib.sh
source scripts/backup_crypt.sh
source scripts/backup_store.sh
source scripts/backup_lock.sh

DB="${1:-}"
[ -n "$DB" ] || { echo "usage: $0 <database>" >&2; exit 2; }

BACKUP_RECIPIENT_KEY="${BACKUP_RECIPIENT_KEY:-}"
BACKUP_ALERT_HOOK="${BACKUP_ALERT_HOOK:-}"
BACKUP_ALERT_TIMEOUT="${BACKUP_ALERT_TIMEOUT:-30}"
BACKUP_KEEP_ROLLING="${BACKUP_KEEP_ROLLING:-28}"
BACKUP_KEEP_WEEKLY="${BACKUP_KEEP_WEEKLY:-8}"

WORK=""
BACKUP_ID=""
# Set only on the deliberate skip below. The EXIT trap treats every non-zero
# status as a failure, so without this the contention path alerted twice --
# backup_skipped and then backup_failed "stage exited 75" -- and a benign
# overlap paged as a fault, which is the one thing that branch exists to avoid.
# A flag rather than exempting 75 in the trap: a 75 from anywhere else is still
# a failure and must still alert.
LOCK_SKIP=0

# Sanitised: an alert path is exactly where a secret gets copied into someone
# else's log.
alert() {  # <event> <detail>
    local event="$1" detail="$2"
    echo "{\"event\":\"$event\",\"database\":\"$DB\",\"backup_id\":\"$BACKUP_ID\",\"detail\":\"$detail\"}" >&2
    [ -n "$BACKUP_ALERT_HOOK" ] || return 0
    # A failing or hanging hook must not bury the failure it was called about.
    if command -v timeout >/dev/null 2>&1; then
        timeout "$BACKUP_ALERT_TIMEOUT" "$BACKUP_ALERT_HOOK" "$event" "$DB" "$BACKUP_ID" "$detail" \
            || echo "alert hook failed or timed out; the original event stands" >&2
    else
        "$BACKUP_ALERT_HOOK" "$event" "$DB" "$BACKUP_ID" "$detail" \
            || echo "alert hook failed; the original event stands" >&2
    fi
}

cleanup() {
    local status=$?
    [ -n "$WORK" ] && rm -rf "$WORK"
    release_backup_lock
    if [ $status -ne 0 ] && [ "$LOCK_SKIP" != 1 ]; then
        alert backup_failed "stage exited $status"
    fi
    return $status
}
trap cleanup EXIT

# Overlap is refused rather than queued: two dumps of one database at once cost
# twice the IO and produce nothing extra, and a waiting run would still hold the
# slot when the next schedule fires.
#
# The lock lives in a runtime directory that is ours and closed to everyone else
# -- see scripts/backup_lock.sh for why that, rather than the locking primitive,
# is the part that matters.
#
# The status is captured through `|| lock_rc=$?` rather than read from `$?` on
# the next line: this script runs under errexit, and there a bare call that
# returns 75 exits before the case is reached -- the skip alert below would
# never have been sent.
lock_rc=0
acquire_backup_lock "backup-$DB" || lock_rc=$?
case $lock_rc in
    0)  ;;
    75) LOCK_SKIP=1
        alert backup_skipped "another run holds the lock"
        exit 75 ;;   # EX_TEMPFAIL: transient, not a fault
    *)  echo "refusing to run without a usable lock" >&2
        exit 1 ;;
esac
echo "==> lock held via $BACKUP_LOCK_BACKEND in $BACKUP_LOCK_DIR"

store_ready
[ -r "$BACKUP_RECIPIENT_KEY" ] || {
    echo "BACKUP_RECIPIENT_KEY must point at a readable public key" >&2; exit 2; }

BACKUP_ID="$(date -u +%Y%m%dT%H%M%SZ)-$(openssl rand -hex 4)"
WORK="$(mktemp -d)"
echo "==> backup $BACKUP_ID of $DB"

echo "==> dump and fingerprint"
pg_fingerprint "$DB" > "$WORK/fingerprint"
pg_tool pg_dump -U "$PGUSER" -d "$DB" -F c > "$WORK/dump"
[ -s "$WORK/dump" ] || { echo "the dump is empty" >&2; exit 1; }

# The plaintext hash, taken before anything touches the bytes. The manifest
# already carried `payload_sha256`, which is the *ciphertext* -- enough to
# prove the download is intact, and unable to say anything about what comes
# out of the decryption. A restore that decrypts to the wrong bytes with the
# wrong key, or against a subtly different envelope, produces a file that
# passes every check up to `pg_restore` and fails there, or worse, does not.
if command -v sha256sum >/dev/null 2>&1; then
    DUMP_SHA="$(sha256sum "$WORK/dump" | awk '{print $1}')"
else
    DUMP_SHA="$(shasum -a 256 "$WORK/dump" | awk '{print $1}')"
fi

echo "==> compress"
gzip -9 -c "$WORK/dump" > "$WORK/dump.gz"
rm -f "$WORK/dump"

echo "==> encrypt to the recipient"
backup_encrypt "$WORK/dump.gz" "$BACKUP_RECIPIENT_KEY" "$WORK/payload"
rm -f "$WORK/dump.gz"     # no plaintext outlives the run

echo "==> checksum the encrypted payload"
if command -v sha256sum >/dev/null 2>&1; then
    (cd "$WORK" && sha256sum payload.enc > payload.enc.sha256)
else
    (cd "$WORK" && shasum -a 256 payload.enc > payload.enc.sha256)
fi
PAYLOAD_SHA="$(awk '{print $1}' < "$WORK/payload.enc.sha256")"
PAYLOAD_BYTES="$(wc -c < "$WORK/payload.enc" | tr -d ' ')"

cat > "$WORK/metadata.json" <<META
{
  "backup_id": "$BACKUP_ID",
  "database": "$DB",
  "created_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "payload_sha256": "$PAYLOAD_SHA",
  "payload_bytes": $PAYLOAD_BYTES,
  "dump_sha256": "$DUMP_SHA",
  "encryption": "rsa-oaep-sha256 + aes-256-cbc + hmac-sha256",
  "compression": "gzip"
}
META

echo "==> upload"
for part in payload.enc payload.hdr payload.mac payload.key payload.enc.sha256 fingerprint metadata.json; do
    store_put "$WORK/$part" "$BACKUP_ID/$part"
done

echo "==> verify what landed"
REMOTE_BYTES="$(store_size "$BACKUP_ID/payload.enc")"
if [ "$REMOTE_BYTES" != "$PAYLOAD_BYTES" ]; then
    echo "uploaded payload is $REMOTE_BYTES bytes, expected $PAYLOAD_BYTES" >&2
    exit 1
fi
store_get "$BACKUP_ID/payload.enc.sha256" "$WORK/remote.sha256"
[ "$(awk '{print $1}' < "$WORK/remote.sha256")" = "$PAYLOAD_SHA" ] || {
    echo "the uploaded checksum does not match the local one" >&2; exit 1; }

# Last, and only now: this is what makes the backup exist.
echo "==> publish the completion manifest"
cat > "$WORK/manifest.json" <<MANIFEST
{
  "backup_id": "$BACKUP_ID",
  "database": "$DB",
  "completed_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "payload_sha256": "$PAYLOAD_SHA",
  "payload_bytes": $PAYLOAD_BYTES,
  "dump_sha256": "$DUMP_SHA"
}
MANIFEST
store_put "$WORK/manifest.json" "$BACKUP_ID/manifest.json"

echo "==> retention"
if ! ./scripts/backup_retention.sh; then
    # A retention fault must not invalidate the backup that just succeeded.
    alert retention_failed "the new backup is intact; old backups were not pruned"
fi

alert backup_succeeded "$PAYLOAD_BYTES bytes"
echo "backup $BACKUP_ID complete"
