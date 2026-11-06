#!/usr/bin/env python3
"""Encrypted backup export and pull, independent of the model worker.

No installer runs implicitly. The server's forced SSH command must fix --root
to an encrypted export directory. The receiver needs only SSH and the stdlib;
it never receives a decryption key. No retention/deletion is implemented.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading

VERSION = "sora-msi-backup/1"
PARTS = {"payload.enc", "payload.key", "payload.mac", "payload.hdr"}
ID = re.compile(r"\d{8}T\d{6}Z\Z", re.ASCII)
MAX_MANIFEST = 16384
MAX_CIPHER = 8 * 1024**3
RESERVE = 1024**3


def backup_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError("invalid_backup_id")
    datetime.strptime(value, "%Y%m%dT%H%M%SZ")
    return value


def regular(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("not_regular_file")
    return path


def directory(path):
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise ValueError("not_directory")
    return path


def digest(path):
    h = hashlib.sha256()
    with regular(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(raw, expected_id):
    if len(raw) > MAX_MANIFEST:
        raise ValueError("manifest_too_large")
    obj = json.loads(raw)
    if obj.get("version") != VERSION or obj.get("backup_id") != backup_id(expected_id):
        raise ValueError("wrong_manifest_identity")
    stamp = datetime.strptime(expected_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    if obj.get("created_utc") != stamp.isoformat():
        raise ValueError("wrong_backup_time")
    if (stamp - datetime.now(timezone.utc)).total_seconds() > 300:
        raise ValueError("backup_from_future")
    files = obj.get("files")
    if not isinstance(files, dict) or set(files) != PARTS:
        raise ValueError("wrong_part_set")
    for name, item in files.items():
        if not isinstance(item, dict):
            raise ValueError("invalid_part")
        limit = MAX_CIPHER if name == "payload.enc" else 8192
        if type(item.get("bytes")) is not int or not 0 < item["bytes"] <= limit:
            raise ValueError("invalid_part_size")
        if not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise ValueError("invalid_part_digest")
    return obj


def read_manifest(root, ident):
    folder = directory(directory(root) / backup_id(ident))
    path = regular(folder / "manifest.json")
    with path.open("rb") as source:
        raw = source.read(MAX_MANIFEST + 1)
    return load_manifest(raw, ident), raw


def verify(folder, manifest):
    directory(folder)
    for name, item in manifest["files"].items():
        path = regular(folder / name)
        if path.stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
            raise ValueError("part_integrity_failure")


@contextmanager
def lock(root):
    directory(root)
    path = root / ".transport.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("invalid_lock_file")
        if os.name == "nt":
            import msvcrt
            os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def serve(root, request, output):
    # SSH_ORIGINAL_COMMAND is data, never evaluated as a shell command.
    if not isinstance(request, str) or len(request) > 200:
        raise ValueError("invalid_request")
    tokens = request.split(" ")
    if tokens == ["list"]:
        ids = sorted(p.name for p in directory(root).iterdir()
                     if ID.fullmatch(p.name) and not p.is_symlink() and p.is_dir()
                     and (p / "manifest.json").is_file())[-64:]
        output.write(json.dumps(ids).encode())
        return
    if len(tokens) not in (2, 3):
        raise ValueError("invalid_request")
    manifest, raw = read_manifest(root, tokens[1])
    if tokens[0] == "manifest" and len(tokens) == 2:
        output.write(raw)
    elif tokens[0] == "part" and len(tokens) == 3 and tokens[2] in PARTS:
        path = regular(root / tokens[1] / tokens[2])
        if path.stat().st_size != manifest["files"][tokens[2]]["bytes"]:
            raise ValueError("changed_export")
        with path.open("rb") as source:
            shutil.copyfileobj(source, output, 1024 * 1024)
    else:
        raise ValueError("invalid_request")


def export(args):
    os.umask(0o077)
    source = regular(args.dump)
    match = re.fullmatch(r"sora_earth_(\d{8}T\d{6}Z)\.dump", source.name, re.ASCII)
    if not match:
        raise ValueError("unexpected_dump_name")
    ident = backup_id(match[1])
    created = datetime.strptime(ident, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - created).total_seconds()
    if not 0 <= age <= 26 * 3600:
        raise ValueError("source_dump_not_fresh")
    if not 0 < source.stat().st_size < MAX_CIPHER // 2:
        raise ValueError("invalid_dump_size")
    regular(args.recipient)
    checksum = regular(Path(str(source) + ".sha256"))
    if checksum.stat().st_size > 4096:
        raise ValueError("invalid_source_checksum")
    recorded = checksum.read_text(encoding="utf-8").split()[0]
    if not re.fullmatch(r"[0-9a-f]{64}", recorded):
        raise ValueError("invalid_source_checksum")
    if digest(source) != recorded:
        raise ValueError("source_checksum_mismatch")
    args.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock(args.root):
        target = args.root / ident
        if target.exists() or target.is_symlink():
            manifest, _ = read_manifest(args.root, ident)
            if manifest.get("source_dump_sha256") != recorded:
                raise ValueError("immutable_source_changed")
            verify(target, manifest)
            return {"backup_id": ident, "status": "already_exported"}
        if shutil.disk_usage(args.root).free < source.stat().st_size * 3 + RESERVE:
            raise ValueError("insufficient_export_space")
        # Temporary plaintext is root-only and never inside the reader's directory.
        with tempfile.TemporaryDirectory(prefix="sora-plain-") as plain_dir:
            plain = Path(plain_dir)
            before = source.stat()
            shutil.copyfile(source, plain / "dump")
            after = source.stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise ValueError("source_changed")
            metadata = {"version": VERSION, "backup_id": ident,
                        "dump_sha256": digest(plain / "dump"),
                        "schema_revision": "inside_dump_not_inspected"}
            if metadata["dump_sha256"] != recorded:
                raise ValueError("source_checksum_mismatch")
            (plain / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            with tarfile.open(plain / "archive.tar", "w") as archive:
                for name in ("dump", "metadata.json"):
                    archive.add(plain / name, arcname=name)
            stage = Path(tempfile.mkdtemp(prefix=".incoming-", dir=args.root))
            try:
                env = {**os.environ, "BACKUP_PYTHON": args.crypto_python}
                subprocess.run(["bash", "-c", 'set -euo pipefail; source "$1"; backup_encrypt "$2" "$3" "$4"',
                                "backup-export", str(Path(__file__).with_name("backup_crypt.sh")),
                                str(plain / "archive.tar"), str(args.recipient.resolve()), str(stage / "payload")],
                               check=True, timeout=600, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                files = {name: {"bytes": (stage / name).stat().st_size, "sha256": digest(stage / name)}
                         for name in sorted(PARTS)}
                manifest = {"version": VERSION, "backup_id": ident,
                            "created_utc": created.isoformat(),
                            "source_dump_sha256": recorded, "files": files}
                raw = json.dumps(manifest, indent=2).encode()
                load_manifest(raw, ident)
                (stage / "manifest.json").write_bytes(raw)
                if args.reader_group:
                    # Root is the sole writer; the dedicated reader gets group read.
                    import grp
                    gid = grp.getgrnam(args.reader_group).gr_gid
                    for folder in (args.root, stage):
                        os.chown(folder, -1, gid)
                        folder.chmod(0o750)
                    for path in stage.iterdir():
                        os.chown(path, -1, gid)
                        path.chmod(0o640)
                stage.rename(target)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
    return {"backup_id": ident, "status": "exported"}


class SSH:
    def __init__(self, args):
        if not re.fullmatch(r"[a-z_][a-z0-9_-]*@[a-zA-Z0-9.-]+", args.host):
            raise ValueError("invalid_ssh_destination")
        regular(args.identity)
        regular(args.known_hosts)
        def ssh_path(path):
            if args.ssh_binary.lower().endswith(".exe") and os.name != "nt":
                resolved = str(path.resolve())
                match = re.fullmatch(r"/mnt/([a-z])/(.+)", resolved)
                if not match:
                    raise ValueError("windows_ssh_requires_drive_paths")
                # Forward slashes also avoid SSH config's backslash interpretation.
                return match[1].upper() + ":/" + match[2]
            return str(path)
        self.prefix = [args.ssh_binary, "-T", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                       "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=15",
                       "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
                       "-o", "UserKnownHostsFile=" + ssh_path(args.known_hosts),
                       "-i", ssh_path(args.identity), args.host]

    def fetch(self, request, destination, limit):
        # stdout is copied as bytes; PowerShell text redirection is never used.
        with open(os.devnull, "wb") as errors:
            process = subprocess.Popen(self.prefix + [request], stdout=subprocess.PIPE, stderr=errors)
            timer = threading.Timer(600, process.kill)
            timer.daemon = True
            timer.start()
            try:
                total = 0
                for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
                    total += len(chunk)
                    if total > limit:
                        raise ValueError("download_too_large")
                    destination.write(chunk)
                if process.wait(timeout=30):
                    raise ValueError("ssh_request_failed")
            finally:
                timer.cancel()
                if process.poll() is None:
                    process.kill()
                process.wait()
                process.stdout.close()


def fetch_bytes(remote, request, limit):
    import io
    output = io.BytesIO()
    remote.fetch(request, output, limit)
    return output.getvalue()


def pull(root, remote):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock(root):
        completed = root / "completed"
        incoming = root / "incoming"
        for path in (completed, incoming):
            path.mkdir(exist_ok=True, mode=0o700)
            directory(path)
        ids = json.loads(fetch_bytes(remote, "list", MAX_MANIFEST))
        if not isinstance(ids, list) or len(ids) > 64 or not ids or len(set(ids)) != len(ids):
            raise ValueError("invalid_or_empty_backup_list")
        ids = sorted(backup_id(ident) for ident in ids)
        received = []
        # Newest first: interruption cannot starve the fresh recovery point.
        for ident in reversed(ids):
            raw = fetch_bytes(remote, "manifest " + ident, MAX_MANIFEST)
            manifest = load_manifest(raw, ident)
            target = completed / ident
            if target.exists() or target.is_symlink():
                local, local_raw = read_manifest(completed, ident)
                if local_raw != raw:
                    raise ValueError("immutable_manifest_changed")
                verify(target, local)
                continue
            total = sum(item["bytes"] for item in manifest["files"].values())
            if shutil.disk_usage(root).free < total + RESERVE:
                raise ValueError("insufficient_receiver_space")
            stage = Path(tempfile.mkdtemp(prefix=".part-", dir=incoming))
            try:
                for name in sorted(PARTS):
                    with (stage / name).open("xb") as destination:
                        remote.fetch("part " + ident + " " + name, destination, manifest["files"][name]["bytes"])
                        destination.flush()
                        os.fsync(destination.fileno())
                verify(stage, manifest)
                with (stage / "manifest.json").open("xb") as marker:
                    marker.write(raw)
                    marker.flush()
                    os.fsync(marker.fileno())
                stage.rename(target)
                received.append(ident)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        age = (datetime.now(timezone.utc) - datetime.strptime(ids[-1], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)).total_seconds()
        return {"status": "received", "new_backups": received,
                "latest_backup": ids[-1], "backup_age_seconds": round(age),
                "freshness": "pass" if -300 <= age <= 26 * 3600 else "fail",
                "decryption": "not_checked", "retention": "no_deletion"}


def status(root):
    completed = directory(directory(root) / "completed")
    ids = sorted(p.name for p in completed.iterdir()
                 if ID.fullmatch(p.name) and p.is_dir() and not p.is_symlink())
    if not ids:
        raise ValueError("no_completed_backups")
    manifest, _ = read_manifest(completed, ids[-1])
    verify(completed / ids[-1], manifest)
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(manifest["created_utc"])).total_seconds()
    return {"latest_backup": ids[-1], "verified_completed_count": 1,
            "completed_directory_count": len(ids), "backup_age_seconds": round(age),
            "freshness": "pass" if -300 <= age <= 26 * 3600 else "fail",
            "decryption": "not_checked", "scope": "latest encrypted backup only"}


def restore_files(args):
    """Mac/operator only: verify and decrypt into a NEW private directory.

    Does not contact a database. Tar members are copied explicitly, never extracted
    by pathname. The real PostgreSQL restore needs its own isolated target.
    """
    os.umask(0o077)
    source = directory(args.backup)
    manifest, _ = read_manifest(source.parent, source.name)
    verify(source, manifest)
    regular(args.identity)
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("restore_output_exists")
    directory(args.output.parent)
    if shutil.disk_usage(args.output.parent).free < manifest["files"]["payload.enc"]["bytes"] * 3 + RESERVE:
        raise ValueError("insufficient_restore_space")
    stage = Path(tempfile.mkdtemp(prefix=".restore-", dir=args.output.parent))
    try:
        archive = stage / "archive.tar"
        env = {**os.environ, "BACKUP_PYTHON": args.crypto_python}
        subprocess.run(["bash", "-c", 'set -euo pipefail; source "$1"; backup_decrypt "$2" "$3" "$4"',
                        "backup-restore-files", str(Path(__file__).with_name("backup_crypt.sh")),
                        str(source.resolve() / "payload"), str(args.identity.resolve()), str(archive.resolve())],
                       check=True, timeout=600, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with tarfile.open(archive, "r:") as bundle:
            members = []
            for member in bundle:
                members.append(member)
                if len(members) > 2:
                    raise ValueError("too_many_archive_members")
            if len(members) != 2 or {m.name for m in members} != {"dump", "metadata.json"}:
                raise ValueError("invalid_archive_members")
            for member in members:
                limit = MAX_CIPHER if member.name == "dump" else MAX_MANIFEST
                if not member.isfile() or not 0 < member.size <= limit:
                    raise ValueError("invalid_archive_member")
                with bundle.extractfile(member) as incoming, (stage / member.name).open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
        metadata = json.loads((stage / "metadata.json").read_bytes())
        if (metadata.get("version") != VERSION or metadata.get("backup_id") != source.name
                or metadata.get("dump_sha256") != digest(stage / "dump")):
            raise ValueError("decrypted_dump_integrity_failure")
        archive.unlink()
        stage.rename(args.output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return {"backup_id": source.name, "status": "files_restored", "database_restore": "not_performed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    out = sub.add_parser("export")
    out.add_argument("--root", type=Path, required=True)
    out.add_argument("--dump", type=Path, required=True)
    out.add_argument("--recipient", type=Path, required=True)
    out.add_argument("--crypto-python", default=sys.executable)
    out.add_argument("--reader-group", default=None)
    latest = sub.add_parser("export-latest")
    latest.add_argument("--root", type=Path, required=True)
    latest.add_argument("--source", type=Path, required=True)
    latest.add_argument("--recipient", type=Path, required=True)
    latest.add_argument("--crypto-python", default=sys.executable)
    latest.add_argument("--reader-group", default=None)
    reader = sub.add_parser("serve")
    reader.add_argument("--root", type=Path, required=True)
    client = sub.add_parser("pull")
    client.add_argument("--root", type=Path, required=True)
    client.add_argument("--host", required=True)
    client.add_argument("--identity", type=Path, required=True)
    client.add_argument("--known-hosts", type=Path, required=True)
    client.add_argument("--ssh-binary", default="ssh")
    probe = sub.add_parser("status")
    probe.add_argument("--root", type=Path, required=True)
    recovery = sub.add_parser("restore-files")
    recovery.add_argument("--backup", type=Path, required=True)
    recovery.add_argument("--identity", type=Path, required=True)
    recovery.add_argument("--output", type=Path, required=True)
    recovery.add_argument("--crypto-python", default=sys.executable)
    args = parser.parse_args()
    try:
        if args.action == "export-latest":
            candidates = sorted(p for p in directory(args.source).iterdir()
                                if re.fullmatch(r"sora_earth_\d{8}T\d{6}Z\.dump", p.name, re.ASCII))
            if not candidates:
                raise ValueError("no_local_dumps")
            args.dump = candidates[-1]
        if args.action == "serve":
            serve(args.root, os.environ.get("SSH_ORIGINAL_COMMAND", ""), sys.stdout.buffer)
        else:
            if args.action in ("export", "export-latest"):
                result = export(args)
            elif args.action == "status":
                result = status(args.root)
            elif args.action == "restore-files":
                result = restore_files(args)
            else:
                result = pull(args.root, SSH(args))
            print(json.dumps(result))
            if result.get("freshness") == "fail":
                return 1
    except Exception:
        # No paths, native stderr, secrets or remote contents in the operator log.
        print("backup_transport_failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
