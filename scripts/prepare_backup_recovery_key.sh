#!/usr/bin/env bash
# Create a NEW owner-local key directory. Never prints or overwrites a private key.
set -euo pipefail
umask 077
[ "$#" -eq 1 ] || { echo 'usage: bash scripts/prepare_backup_recovery_key.sh NEW_DIRECTORY' >&2; exit 2; }
key_dir="$1"
case "$key_dir" in /*) ;; *) echo 'absolute directory required' >&2; exit 2 ;; esac
if [ -e "$key_dir" ] || [ -L "$key_dir" ]; then echo 'refusing to replace existing key directory' >&2; exit 2; fi
command -v openssl >/dev/null
mkdir -p "$(dirname "$key_dir")"
mkdir -m 0700 "$key_dir"
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "$key_dir/recovery.pem" 2>/dev/null
chmod 0600 "$key_dir/recovery.pem"
openssl pkey -in "$key_dir/recovery.pem" -check -noout >/dev/null 2>&1
openssl pkey -in "$key_dir/recovery.pem" -pubout -out "$key_dir/recipient.pem" 2>/dev/null
openssl pkey -pubin -in "$key_dir/recipient.pem" -outform DER 2>/dev/null \
  | openssl dgst -sha256 > "$key_dir/public-key.sha256"
echo 'RECOVERY_KEY_CREATED_LOCALLY; USB copy not yet verified.'
echo "Public fingerprint: $(cat "$key_dir/public-key.sha256")"
