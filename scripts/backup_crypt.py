#!/usr/bin/env python3
"""The symmetric half of the backup envelope, kept out of argv.

`openssl enc` accepts a raw key only as `-K <hex>`, and `openssl dgst` its MAC
key only as `-macopt hexkey:<hex>`. Both land in the process's argument list,
and on Linux /proc/<pid>/cmdline is mode 444 -- world-readable. Measured: while
root ran `openssl enc`, an unprivileged user read the full AES key out of
/proc/<pid>/cmdline. Combined with a ciphertext that is deliberately stored
off-host, that is the whole guarantee this envelope exists to provide.

There is no way around it with the openssl CLI: every option that keeps a secret
out of argv (`-pass fd:`, `-pass file:`) feeds a passphrase through a KDF, which
is a different envelope, and the MAC key has no file-based option at all. So the
symmetric half lives here instead.

The key material is read from a file the caller creates 0600. Only its path
reaches argv, and a path is not a secret.

The wire format is unchanged. This produces and consumes exactly what the
openssl invocations produced -- AES-256-CBC with PKCS#7 padding, no salt header,
HMAC-SHA256 over the concatenated inputs -- which is asserted by a test that
encrypts here and decrypts with openssl, and the reverse.

Input is streamed. A database dump does not belong in memory in one piece.
"""
import argparse
import hashlib
import hmac
import sys
from pathlib import Path

try:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError:  # pragma: no cover - exercised by the shell preflight
    sys.exit(
        "backup_crypt.py needs the 'cryptography' package.\n"
        "It is in requirements.txt, so an interpreter that has the application's\n"
        "dependencies already satisfies this. Point BACKUP_PYTHON at that\n"
        "interpreter, or install it for the one on PATH."
    )

CHUNK = 1 << 20


def _material(path: str) -> tuple[bytes, bytes, bytes]:
    """80 sealed bytes: AES key, MAC key, IV -- the split the envelope documents."""
    raw = Path(path).read_bytes()
    if len(raw) != 80:
        sys.exit(f"key material must be 80 bytes, got {len(raw)}")
    return raw[0:32], raw[32:64], raw[64:80]


def _encrypt(material: str, src: str, dst: str) -> None:
    enc_key, _, iv = _material(material)
    encryptor = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).encryptor()
    padder = padding.PKCS7(128).padder()
    with open(src, "rb") as f, open(dst, "wb") as g:
        while chunk := f.read(CHUNK):
            g.write(encryptor.update(padder.update(chunk)))
        g.write(encryptor.update(padder.finalize()))
        g.write(encryptor.finalize())


def _decrypt(material: str, src: str, dst: str) -> None:
    enc_key, _, iv = _material(material)
    decryptor = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).decryptor()
    unpadder = padding.PKCS7(128).unpadder()
    with open(src, "rb") as f, open(dst, "wb") as g:
        while chunk := f.read(CHUNK):
            g.write(unpadder.update(decryptor.update(chunk)))
        g.write(unpadder.update(decryptor.finalize()))
        g.write(unpadder.finalize())


def _digest(material: str, files: list[str]) -> str:
    _, mac_key, _ = _material(material)
    h = hmac.new(mac_key, digestmod=hashlib.sha256)
    for path in files:
        with open(path, "rb") as f:
            while chunk := f.read(CHUNK):
                h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("encrypt", "decrypt"):
        p = sub.add_parser(name)
        p.add_argument("--material", required=True)
        p.add_argument("--in", dest="src", required=True)
        p.add_argument("--out", dest="dst", required=True)

    p = sub.add_parser("mac")
    p.add_argument("--material", required=True)
    p.add_argument("files", nargs="+")

    # Comparison belongs here rather than in the shell: `[ "$a" != "$b" ]`
    # compares byte by byte and stops at the first difference.
    p = sub.add_parser("verify-mac")
    p.add_argument("--material", required=True)
    p.add_argument("--expected", required=True)
    p.add_argument("files", nargs="+")

    args = parser.parse_args()

    if args.command == "encrypt":
        _encrypt(args.material, args.src, args.dst)
    elif args.command == "decrypt":
        _decrypt(args.material, args.src, args.dst)
    elif args.command == "mac":
        print(_digest(args.material, args.files))
    elif args.command == "verify-mac":
        expected = Path(args.expected).read_text().split()[-1].strip()
        if not hmac.compare_digest(expected, _digest(args.material, args.files)):
            print("mac mismatch", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
