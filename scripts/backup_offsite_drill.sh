#!/usr/bin/env bash
# Prove that the *off-site copy* can bring the database back.
#
#   BACKUP_IDENTITY_KEY=/path/to/identity.pem \
#   PG_CONTAINER=sora-drill-pg \
#       ./scripts/backup_offsite_drill.sh [backup-id]
#
# Separate from scripts/backup_restore_drill.sh on purpose. That one answers
# "can a dump restore this database", starting from a file it just wrote: it
# never downloads, never decrypts, and would pass with an empty bucket. This
# one starts where a real disaster starts -- with nothing but the store.
#
# It answers, in order: is there a *completed* set, does the manifest describe
# what is actually there, does the ciphertext arrive intact, does it decrypt to
# the bytes that were taken, is the archive readable, does it restore, and is
# what comes back the database that was fingerprinted at backup time.
#
# Destructive only to a temporary database of its own naming, dropped on every
# exit path. It never writes to the store.
set -euo pipefail

cd "$(dirname "$0")/.."
# The `source=` directives name the files for anyone running `shellcheck -x`;
# CI does not pass it, so SC1091 is silenced here rather than left as three
# permanent findings in a script held to every severity.
# shellcheck source=scripts/pg_lib.sh disable=SC1091
source scripts/pg_lib.sh
# shellcheck source=scripts/backup_crypt.sh disable=SC1091
source scripts/backup_crypt.sh
# shellcheck source=scripts/backup_store.sh disable=SC1091
source scripts/backup_store.sh

IDENTITY="${BACKUP_IDENTITY_KEY:-}"
WANTED="${1:-}"
FAILURES=0

[ -r "$IDENTITY" ] || {
    echo "BACKUP_IDENTITY_KEY must point at the private identity" >&2; exit 2; }

note()  { printf '\n=== %s ===\n' "$*"; }
check() {  # check <label> <expected> <actual>
    if [ "$2" = "$3" ]; then
        printf '  ok    %-38s %s\n' "$1" "$3"
    else
        printf '  FAIL  %-38s expected %s, got %s\n' "$1" "$2" "$3"
        FAILURES=$((FAILURES + 1))
    fi
}
sha_of() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
    else shasum -a 256 "$1" | awk '{print $1}'; fi
}
field() {  # field <name> <file>
    sed -n "s/.*\"$1\"[^\"]*\"\([^\"]*\)\".*/\1/p" "$2"
}

store_ready

WORK="$(mktemp -d)"
DRILL_DB="offsite_drill_$$"
# Every exit path: success, a failed check, a signal, `set -e` on any command.
# The plaintext dump lives in WORK, so leaving it behind is a disclosure, and a
# leftover database is a surprise for whoever runs this next.
# shellcheck disable=SC2329  # invoked by the trap below, not by name
cleanup() {
    rm -rf "$WORK"
    pg_tool_stdin psql -U "$PGUSER" -d postgres -tAc \
        "DROP DATABASE IF EXISTS \"$DRILL_DB\" WITH (FORCE)" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

note "1. choose a completed set"
# Never "the newest object". `store_list_backups` lists the ids that have a
# manifest, and the manifest is written last -- so a set still uploading, or one
# whose upload died, is not a candidate however new it is.
COMPLETED="$(store_list_backups || true)"
[ -n "$COMPLETED" ] || { echo "  no completed backup in the store" >&2; exit 1; }
if [ -n "$WANTED" ]; then
    BACKUP_ID="$WANTED"
    echo "$COMPLETED" | grep -qx "$BACKUP_ID" || {
        echo "  $BACKUP_ID has no manifest: it is not a completed backup" >&2; exit 1; }
else
    BACKUP_ID="$(echo "$COMPLETED" | tail -1)"
fi
echo "  completed sets : $(echo "$COMPLETED" | wc -l | tr -d ' ')"
echo "  chosen         : $BACKUP_ID"

note "2. the manifest, and what it says the set contains"
store_get "$BACKUP_ID/manifest.json" "$WORK/manifest.json"
MANIFEST_SHA="$(field payload_sha256 "$WORK/manifest.json")"
MANIFEST_DUMP_SHA="$(field dump_sha256 "$WORK/manifest.json")"
MANIFEST_BYTES="$(sed -n 's/.*"payload_bytes"[^0-9]*\([0-9]*\).*/\1/p' "$WORK/manifest.json")"
[ -n "$MANIFEST_SHA" ] || { echo "  the manifest carries no payload_sha256" >&2; exit 1; }
echo "  payload_sha256 : $MANIFEST_SHA"
echo "  payload_bytes  : $MANIFEST_BYTES"

# Every part the restore needs must be present *before* anything is downloaded.
# A manifest names a set; it does not prove the set arrived.
for part in payload.enc payload.hdr payload.mac payload.key; do
    store_exists "$BACKUP_ID/$part" \
        || { echo "  the set is incomplete: $part is missing" >&2; exit 1; }
done
check "all encryption parts present" "4" "4"

note "3. download"
for part in payload.enc payload.hdr payload.mac payload.key; do
    store_get "$BACKUP_ID/$part" "$WORK/$part"
done
# The fingerprint taken at backup time. Absent on sets written before it was
# uploaded, and the comparison is skipped rather than failed in that case.
HAVE_FINGERPRINT=0
if store_exists "$BACKUP_ID/fingerprint"; then
    store_get "$BACKUP_ID/fingerprint" "$WORK/before.fingerprint"
    HAVE_FINGERPRINT=1
fi

note "4. the ciphertext is the one the manifest describes"
LOCAL_BYTES="$(wc -c < "$WORK/payload.enc" | tr -d ' ')"
check "downloaded bytes" "$MANIFEST_BYTES" "$LOCAL_BYTES"
check "ciphertext sha256" "$MANIFEST_SHA" "$(sha_of "$WORK/payload.enc")"
# Distinct wording on purpose. `backup_crypt.sh` also says "refusing to
# decrypt" when authentication fails, and a test that matched that substring
# could not tell "stopped before decrypting" from "the decryption stopped it".
[ "$FAILURES" = "0" ] || {
    echo "download does not match the manifest -- stopping before decryption" >&2
    exit 1
}

note "5. decrypt"
backup_decrypt "$WORK/payload" "$IDENTITY" "$WORK/dump.gz"
gunzip -c "$WORK/dump.gz" > "$WORK/dump"
[ -s "$WORK/dump" ] || { echo "  the decrypted dump is empty" >&2; exit 1; }

note "6. the plaintext is the one that was taken"
if [ -n "$MANIFEST_DUMP_SHA" ]; then
    check "dump sha256" "$MANIFEST_DUMP_SHA" "$(sha_of "$WORK/dump")"
else
    # Sets written before the field existed. Said out loud rather than passed
    # over: an unverifiable step must not read as a verified one.
    echo "  SKIP  the manifest predates dump_sha256; the plaintext is unverified"
fi

note "7. the archive is readable"
if pg_tool_stdin pg_restore --list < "$WORK/dump" > "$WORK/toc" 2>"$WORK/toc.err"; then
    echo "  ok    table of contents                    $(wc -l < "$WORK/toc" | tr -d ' ') entries"
else
    echo "  FAIL  pg_restore --list refused the archive"
    sed 's/^/        /' "$WORK/toc.err" >&2
    FAILURES=$((FAILURES + 1))
fi

note "8. restore into a temporary database"
pg_tool_stdin psql -U "$PGUSER" -d postgres -tAc \
    "DROP DATABASE IF EXISTS \"$DRILL_DB\"" >/dev/null
pg_tool_stdin psql -U "$PGUSER" -d postgres -tAc \
    "CREATE DATABASE \"$DRILL_DB\"" >/dev/null
if pg_tool_stdin pg_restore -U "$PGUSER" -d "$DRILL_DB" \
        --single-transaction --exit-on-error --no-owner < "$WORK/dump" >/dev/null; then
    echo "  ok    restored into $DRILL_DB"
else
    echo "  FAIL  the restore did not complete"
    FAILURES=$((FAILURES + 1))
fi

note "9. what came back"
if [ "$HAVE_FINGERPRINT" = "1" ]; then
    pg_fingerprint "$DRILL_DB" > "$WORK/after.fingerprint"
    NORM="${PYTHON:-python3} scripts/pg_fingerprint_normalise.py"
    $NORM < "$WORK/before.fingerprint" > "$WORK/before.normalised"
    $NORM < "$WORK/after.fingerprint"  > "$WORK/after.normalised"
    if diff -u "$WORK/before.normalised" "$WORK/after.normalised" > "$WORK/fingerprint.diff"; then
        echo "  ok    fingerprint matches the one taken at backup time"
    else
        echo "  FAIL  the restored database differs from what was backed up"
        head -40 "$WORK/fingerprint.diff" | sed 's/^/        /'
        FAILURES=$((FAILURES + 1))
    fi
else
    echo "  SKIP  the set carries no fingerprint; content was not compared"
fi

note "result"
if [ "$FAILURES" = "0" ]; then
    echo "  the off-site copy $BACKUP_ID restores and matches."
else
    echo "  $FAILURES check(s) failed for $BACKUP_ID."
fi
exit "$FAILURES"
