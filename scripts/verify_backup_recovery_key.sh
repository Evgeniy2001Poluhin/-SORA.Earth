#!/usr/bin/env bash
# Owner supplies TWO paths; only public fingerprints reach stdout.
set -euo pipefail
[ "$#" -eq 2 ] || { echo 'usage: bash scripts/verify_backup_recovery_key.sh MAC_PRIVATE_KEY USB_PRIVATE_KEY' >&2; exit 2; }
[ ! "$1" -ef "$2" ] || { echo 'two distinct key files required' >&2; exit 2; }
for key_file in "$1" "$2"; do
    [ -f "$key_file" ] && [ ! -L "$key_file" ] || { echo 'key missing or symlink' >&2; exit 2; }
    openssl pkey -in "$key_file" -check -noout >/dev/null 2>&1
done
first="$(openssl pkey -in "$1" -pubout -outform DER 2>/dev/null | openssl dgst -sha256)"
second="$(openssl pkey -in "$2" -pubout -outform DER 2>/dev/null | openssl dgst -sha256)"
[ "$first" = "$second" ] || { echo 'RECOVERY_KEYS_DIFFER' >&2; exit 1; }
echo 'RECOVERY_KEY_COPIES_MATCH'
echo 'Check the second file is on the intended external device separately.'
echo "Public fingerprint: $first"
