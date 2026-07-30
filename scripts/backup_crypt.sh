#!/usr/bin/env bash
# Recipient-based encryption for backups, built on OpenSSL alone.
#
# The backup host must not be able to read its own backups. A passphrase --
# symmetric encryption -- fails that: whatever the writer needs in order to
# encrypt is also enough to decrypt, so compromising the machine that runs the
# schedule hands over the archive with it.
#
# The scheme is hybrid, which is what age and GPG do internally:
#
#   material  = 80 random bytes, sealed to an RSA public key
#               [0:32]  AES key
#               [32:64] HMAC key
#               [64:80] IV
#   payload   = AES-256-CBC under that key
#   header    = version and algorithm identifiers, in the clear
#   mac       = HMAC-SHA256 over header || ciphertext
#
# The IV travels inside the sealed material rather than beside the ciphertext:
# it need not be secret, but sealing it removes a file that could be corrupted
# independently of the thing that authenticates it.
#
# The private key never reaches the backup host. Restoring is a separate,
# deliberate act on a machine that holds it.
#
# CBC plus a separate HMAC rather than an AEAD mode: `openssl enc` offers no
# authenticated mode, and encrypt-then-MAC gives the same guarantee without
# pretending an unauthenticated interface is authenticated.
#
# This is a versioned OpenSSL envelope, not a reimplementation of age. It is
# narrower and has had none of that format's review; the header exists so a
# future version can change algorithms without a reader guessing, and so that
# an attacker cannot rewrite the algorithm line and be believed.
#
# The header is authenticated together with the ciphertext. Signing only the
# ciphertext would leave the parameters describing it unprotected, which is how
# downgrade attacks on otherwise sound constructions work.
# No `set -e` here: this file is sourced, so any option it sets lands in the
# caller's shell. Every script that sources it already sets its own, and a
# library that overrides them is deciding for code it cannot see -- which is how
# a lock helper turned "someone else holds it" into a silent exit.

# Bumped whenever the envelope changes shape. A reader that does not recognise
# the version refuses rather than guessing.
BACKUP_ENVELOPE_VERSION="sora-backup-envelope/1"
BACKUP_ENVELOPE_ALGS="aes-256-cbc/hmac-sha256/rsa-oaep-sha256"

# The symmetric half runs in scripts/backup_crypt.py, because openssl enc takes
# a raw key only as `-K <hex>` and openssl dgst its MAC key only as
# `-macopt hexkey:<hex>` -- both of which land in /proc/<pid>/cmdline, mode 444.
# Measured: while root ran openssl enc, an unprivileged user read the AES key
# straight out of it. The keys now never reach argv, and never become shell
# variables either; the helper reads them from the sealed-material file, which
# the caller creates 0600.
#
# The RSA half stays on the openssl CLI: -inkey takes a path, and a path is not
# a secret.
BACKUP_PYTHON="${BACKUP_PYTHON:-python3}"
CRYPT_HELPER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/backup_crypt.py"

_crypt_helper() {
    "$BACKUP_PYTHON" "$CRYPT_HELPER" "$@"
}

# Checked once, loudly, rather than discovered at 03:00. An undeclared tool that
# is merely usually present is how a schedule stops without saying so.
_require_crypt_helper() {
    [ -r "$CRYPT_HELPER" ] || {
        echo "crypt: helper not found at $CRYPT_HELPER" >&2; return 1; }
    if ! "$BACKUP_PYTHON" -c 'import cryptography' >/dev/null 2>&1; then
        echo "crypt: $BACKUP_PYTHON cannot import 'cryptography'." >&2
        echo "       It is in requirements.txt, so the interpreter that has the" >&2
        echo "       application's dependencies already satisfies this. Set" >&2
        echo "       BACKUP_PYTHON to that interpreter." >&2
        return 1
    fi
}

backup_encrypt() {  # <plaintext> <recipient-pubkey.pem> <out-prefix>
    local plain="$1" recipient="$2" prefix="$3"
    [ -r "$plain" ] || { echo "encrypt: no such file: $plain" >&2; return 1; }
    [ -r "$recipient" ] || { echo "encrypt: no recipient key: $recipient" >&2; return 1; }

    _require_crypt_helper || return 1

    local material; material="$(mktemp)"
    chmod 600 "$material"
    openssl rand 80 > "$material"

    printf '%s\n%s\n' "$BACKUP_ENVELOPE_VERSION" "$BACKUP_ENVELOPE_ALGS" > "$prefix.hdr"
    if ! _crypt_helper encrypt --material "$material" --in "$plain" --out "$prefix.enc"; then
        rm -f "$material"; echo "encrypt: the payload could not be encrypted" >&2; return 1
    fi
    # Header first, then ciphertext: one MAC over both.
    if ! _crypt_helper mac --material "$material" "$prefix.hdr" "$prefix.enc" > "$prefix.mac"; then
        rm -f "$material" "$prefix.mac"; echo "encrypt: the mac could not be computed" >&2; return 1
    fi
    openssl pkeyutl -encrypt -pubin -inkey "$recipient" \
        -pkeyopt rsa_padding_mode:oaep -pkeyopt rsa_oaep_md:sha256 \
        -in "$material" -out "$prefix.key"

    rm -f "$material"
}

backup_decrypt() {  # <in-prefix> <identity.pem> <out-plaintext>
    local prefix="$1" identity="$2" out="$3"
    [ -r "$identity" ] || { echo "decrypt: no identity key: $identity" >&2; return 1; }
    local part
    for part in enc mac key hdr; do
        [ -r "$prefix.$part" ] || { echo "decrypt: missing $prefix.$part" >&2; return 1; }
    done

    _require_crypt_helper || return 1

    local material; material="$(mktemp)"
    chmod 600 "$material"

    if ! openssl pkeyutl -decrypt -inkey "$identity" \
            -pkeyopt rsa_padding_mode:oaep -pkeyopt rsa_oaep_md:sha256 \
            -in "$prefix.key" -out "$material" 2>/dev/null; then
        rm -f "$material"
        echo "decrypt: the sealed key does not open with this identity" >&2
        return 1
    fi

    # Refuse an envelope this reader does not understand, before touching the
    # payload at all.
    local got_version
    got_version="$(head -n 1 "$prefix.hdr")"
    if [ "$got_version" != "$BACKUP_ENVELOPE_VERSION" ]; then
        rm -f "$material"
        echo "decrypt: unknown envelope version: $got_version" >&2
        return 1
    fi

    # Authenticate before decrypting, over the header as well as the ciphertext.
    # A modified payload -- or a rewritten algorithm line -- has to be refused
    # outright, not decrypted and then judged. The comparison is constant-time,
    # inside the helper.
    if ! _crypt_helper verify-mac --material "$material" --expected "$prefix.mac" \
            "$prefix.hdr" "$prefix.enc" 2>/dev/null; then
        rm -f "$material"
        echo "decrypt: payload failed authentication -- refusing to decrypt" >&2
        return 1
    fi

    if ! _crypt_helper decrypt --material "$material" --in "$prefix.enc" --out "$out" 2>/dev/null; then
        rm -f "$material" "$out"
        echo "decrypt: payload could not be decrypted" >&2
        return 1
    fi
    rm -f "$material"
}
