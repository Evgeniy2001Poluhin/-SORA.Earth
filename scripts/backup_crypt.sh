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
#   mac       = HMAC-SHA256 over the ciphertext
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
set -euo pipefail

_material_parts() {  # <material-file> -> sets ENC_KEY, MAC_KEY, IV
    local f="$1"
    ENC_KEY="$(dd if="$f" bs=1 skip=0  count=32 2>/dev/null | xxd -p -c 64)"
    MAC_KEY="$(dd if="$f" bs=1 skip=32 count=32 2>/dev/null | xxd -p -c 64)"
    IV="$(dd if="$f" bs=1 skip=64 count=16 2>/dev/null | xxd -p -c 32)"
}

_wipe() { unset ENC_KEY MAC_KEY IV; }

backup_encrypt() {  # <plaintext> <recipient-pubkey.pem> <out-prefix>
    local plain="$1" recipient="$2" prefix="$3"
    [ -r "$plain" ] || { echo "encrypt: no such file: $plain" >&2; return 1; }
    [ -r "$recipient" ] || { echo "encrypt: no recipient key: $recipient" >&2; return 1; }

    local material; material="$(mktemp)"
    chmod 600 "$material"
    openssl rand 80 > "$material"
    _material_parts "$material"

    openssl enc -aes-256-cbc -K "$ENC_KEY" -iv "$IV" -in "$plain" -out "$prefix.enc"
    openssl dgst -sha256 -mac HMAC -macopt "hexkey:$MAC_KEY" \
        -out "$prefix.mac" "$prefix.enc"
    openssl pkeyutl -encrypt -pubin -inkey "$recipient" \
        -pkeyopt rsa_padding_mode:oaep -pkeyopt rsa_oaep_md:sha256 \
        -in "$material" -out "$prefix.key"

    rm -f "$material"
    _wipe
}

backup_decrypt() {  # <in-prefix> <identity.pem> <out-plaintext>
    local prefix="$1" identity="$2" out="$3"
    [ -r "$identity" ] || { echo "decrypt: no identity key: $identity" >&2; return 1; }
    local part
    for part in enc mac key; do
        [ -r "$prefix.$part" ] || { echo "decrypt: missing $prefix.$part" >&2; return 1; }
    done

    local material; material="$(mktemp)"
    chmod 600 "$material"

    if ! openssl pkeyutl -decrypt -inkey "$identity" \
            -pkeyopt rsa_padding_mode:oaep -pkeyopt rsa_oaep_md:sha256 \
            -in "$prefix.key" -out "$material" 2>/dev/null; then
        rm -f "$material"
        echo "decrypt: the sealed key does not open with this identity" >&2
        return 1
    fi
    _material_parts "$material"
    rm -f "$material"

    # Authenticate before decrypting. A modified ciphertext has to be refused
    # outright, not decrypted and then judged.
    local expected actual
    expected="$(awk '{print $NF}' < "$prefix.mac")"
    actual="$(openssl dgst -sha256 -mac HMAC -macopt "hexkey:$MAC_KEY" "$prefix.enc" | awk '{print $NF}')"
    if [ "$expected" != "$actual" ]; then
        _wipe
        echo "decrypt: payload failed authentication -- refusing to decrypt" >&2
        return 1
    fi

    if ! openssl enc -d -aes-256-cbc -K "$ENC_KEY" -iv "$IV" \
            -in "$prefix.enc" -out "$out" 2>/dev/null; then
        _wipe
        rm -f "$out"
        echo "decrypt: payload could not be decrypted" >&2
        return 1
    fi
    _wipe
}
