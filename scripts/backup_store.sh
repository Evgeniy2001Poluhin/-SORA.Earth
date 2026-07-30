#!/usr/bin/env bash
# The object store, behind one seam.
#
# S3 has no atomic rename, so "upload then move into place" -- the pattern that
# makes a local write atomic -- does not exist here. A reader can always catch a
# multi-part upload half finished, and a half-finished payload that looks like a
# backup is worse than no backup: it is discovered during a restore.
#
# So completion is explicit. Every object of a backup is written first, and a
# small manifest is written last. Nothing without a manifest is a backup:
# restore refuses it and retention ignores it. Sweeping the leftovers is a
# separate, unhurried job.
#
# One client, not an abstraction over three. BACKUP_S3_CLIENT names the
# executable so tests can point it at a local double; the default is the AWS CLI,
# which speaks to any S3-compatible endpoint through --endpoint-url.
#
# Credentials come from files, never from argv -- an argument list is readable
# by any process on the host -- and are never echoed.
# No `set -e` here: this file is sourced, so any option it sets lands in the
# caller's shell. Every script that sources it already sets its own, and a
# library that overrides them is deciding for code it cannot see -- which is how
# a lock helper turned "someone else holds it" into a silent exit.

BACKUP_S3_CLIENT="${BACKUP_S3_CLIENT:-aws}"
BACKUP_S3_ENDPOINT="${BACKUP_S3_ENDPOINT:-}"
BACKUP_S3_BUCKET="${BACKUP_S3_BUCKET:-}"
BACKUP_S3_PREFIX="${BACKUP_S3_PREFIX:-sora-backups}"
BACKUP_S3_REGION="${BACKUP_S3_REGION:-us-east-1}"
BACKUP_S3_ACCESS_KEY_FILE="${BACKUP_S3_ACCESS_KEY_FILE:-}"
BACKUP_S3_SECRET_KEY_FILE="${BACKUP_S3_SECRET_KEY_FILE:-}"

store_ready() {
    [ -n "$BACKUP_S3_BUCKET" ] || { echo "store: BACKUP_S3_BUCKET is unset" >&2; return 1; }
    command -v "$BACKUP_S3_CLIENT" >/dev/null 2>&1 ||
        { echo "store: client not found: $BACKUP_S3_CLIENT" >&2; return 1; }
}

_load_credentials() {
    # Exported for the child only, and only if the files exist. Reading them
    # here keeps the values out of every argv on the host.
    if [ -n "$BACKUP_S3_ACCESS_KEY_FILE" ] && [ -r "$BACKUP_S3_ACCESS_KEY_FILE" ]; then
        AWS_ACCESS_KEY_ID="$(< "$BACKUP_S3_ACCESS_KEY_FILE")"; export AWS_ACCESS_KEY_ID
    fi
    if [ -n "$BACKUP_S3_SECRET_KEY_FILE" ] && [ -r "$BACKUP_S3_SECRET_KEY_FILE" ]; then
        AWS_SECRET_ACCESS_KEY="$(< "$BACKUP_S3_SECRET_KEY_FILE")"; export AWS_SECRET_ACCESS_KEY
    fi
    export AWS_DEFAULT_REGION="$BACKUP_S3_REGION"
}

_s3() {
    _load_credentials
    if [ -n "$BACKUP_S3_ENDPOINT" ]; then
        "$BACKUP_S3_CLIENT" --endpoint-url "$BACKUP_S3_ENDPOINT" "$@"
    else
        "$BACKUP_S3_CLIENT" "$@"
    fi
}

store_put() {  # <local-file> <remote-relative-path>
    _s3 s3 cp "$1" "s3://$BACKUP_S3_BUCKET/$BACKUP_S3_PREFIX/$2" >/dev/null
}

store_get() {  # <remote-relative-path> <local-file>
    _s3 s3 cp "s3://$BACKUP_S3_BUCKET/$BACKUP_S3_PREFIX/$1" "$2" >/dev/null
}

store_size() {  # <remote-relative-path> -> bytes on stdout, empty when absent
    _s3 s3api head-object --bucket "$BACKUP_S3_BUCKET" \
        --key "$BACKUP_S3_PREFIX/$1" 2>/dev/null | awk -F'[:, ]+' '/ContentLength/{print $3}'
}

store_exists() { [ -n "$(store_size "$1")" ]; }

store_list_backups() {  # ids of *completed* backups, oldest first
    _s3 s3 ls "s3://$BACKUP_S3_BUCKET/$BACKUP_S3_PREFIX/" --recursive 2>/dev/null |
        awk '{print $NF}' | sed -n 's#.*/\([^/]*\)/manifest.json$#\1#p' | sort
}

store_delete_backup() {  # <backup-id>
    _s3 s3 rm "s3://$BACKUP_S3_BUCKET/$BACKUP_S3_PREFIX/$1/" --recursive >/dev/null
}
