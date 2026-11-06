"""Exercise the actual reader and receiver with corrupted/interrupted peers."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("backup_transport", Path(__file__).parents[1] / "scripts/backup_transport.py")
transport = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transport)


@pytest.fixture
def exported(tmp_path):
    ident = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = tmp_path / "export"
    folder = root / ident
    folder.mkdir(parents=True)
    files = {}
    for name in transport.PARTS:
        value = ("encrypted fixture " + name).encode()
        (folder / name).write_bytes(value)
        files[name] = {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    manifest = {"version": transport.VERSION, "backup_id": ident,
                "created_utc": datetime.strptime(ident, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat(),
                "files": files}
    (folder / "manifest.json").write_text(json.dumps(manifest))
    return root, ident, manifest


class Peer:
    def __init__(self, root):
        self.root = root
        self.calls = []

    def fetch(self, request, destination, limit):
        self.calls.append(request)
        out = io.BytesIO()
        transport.serve(self.root, request, out)
        value = out.getvalue()
        if len(value) > limit:
            raise ValueError("too_large")
        destination.write(value)


def test_positive_pull_repeat_and_reverify(exported, tmp_path):
    root, ident, _ = exported
    peer = Peer(root)
    store = tmp_path / "store"
    result = transport.pull(store, peer)
    assert result["new_backups"] == [ident]
    assert result["freshness"] == "pass"
    assert result["decryption"] == "not_checked"
    assert not list((store / "incoming").iterdir())
    assert transport.status(store)["freshness"] == "pass"
    peer.calls.clear()
    assert transport.pull(store, peer)["new_backups"] == []
    assert not any(call.startswith("part ") for call in peer.calls)
    (store / "completed" / ident / "payload.enc").write_bytes(b"damaged")
    with pytest.raises(ValueError):
        transport.pull(store, peer)


@pytest.mark.parametrize("command", ["", "ls", "list;id", "list\nwhoami", "manifest ../",
    "manifest 20260231T000000Z", "part 20260101T000000Z ../secret", "list extra", "list  ", "x" * 201])
def test_reader_refuses_nonprotocol_requests(exported, command):
    root, _, _ = exported
    with pytest.raises((ValueError, FileNotFoundError)):
        transport.serve(root, command, io.BytesIO())


def test_reader_rejects_part_symlink(exported, tmp_path):
    root, ident, _ = exported
    target = root / ident / "payload.enc"
    target.unlink()
    secret = tmp_path / "secret"
    secret.write_bytes(b"not exported")
    target.symlink_to(secret)
    with pytest.raises(ValueError):
        transport.serve(root, "part " + ident + " payload.enc", io.BytesIO())


@pytest.mark.parametrize("mutation", ["version", "id", "time", "path", "bool_size", "huge_size", "hash"])
def test_manifest_rejected_before_parts(exported, mutation):
    _, ident, original = exported
    doc = copy.deepcopy(original)
    if mutation == "version": doc["version"] = "unsupported"
    if mutation == "id": doc["backup_id"] = "../bad"
    if mutation == "time": doc["created_utc"] = "1990-01-01"
    if mutation == "path": doc["files"]["../outside"] = doc["files"].pop("payload.enc")
    if mutation == "bool_size": doc["files"]["payload.enc"]["bytes"] = True
    if mutation == "huge_size": doc["files"]["payload.enc"]["bytes"] = transport.MAX_CIPHER + 1
    if mutation == "hash": doc["files"]["payload.enc"]["sha256"] = "not-a-hash"
    with pytest.raises(ValueError):
        transport.load_manifest(json.dumps(doc).encode(), ident)


def test_corruption_never_publishes(exported, tmp_path):
    root, ident, _ = exported
    path = root / ident / "payload.enc"
    path.write_bytes(b"x" * path.stat().st_size)
    store = tmp_path / "store"
    with pytest.raises(ValueError, match="integrity"):
        transport.pull(store, Peer(root))
    assert not (store / "completed" / ident).exists()
    assert not list((store / "incoming").iterdir())


def test_interruption_keeps_last_good_and_cleans_partial(exported, tmp_path):
    root, ident, _ = exported
    store = tmp_path / "store"
    good = store / "completed" / "20260101T000000Z"
    good.mkdir(parents=True)
    (good / "keep").write_text("last known good")
    class Interrupted(Peer):
        def fetch(self, request, destination, limit):
            if request.startswith("part "):
                destination.write(b"partial")
                raise OSError("network interrupted")
            return super().fetch(request, destination, limit)
    with pytest.raises(OSError):
        transport.pull(store, Interrupted(root))
    assert (good / "keep").read_text() == "last known good"
    assert not (store / "completed" / ident).exists()
    assert not list((store / "incoming").iterdir())


def test_no_space_never_downloads_parts(exported, tmp_path, monkeypatch):
    root, _, _ = exported
    peer = Peer(root)
    monkeypatch.setattr(transport.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(ValueError, match="space"):
        transport.pull(tmp_path / "store", peer)
    assert not any(call.startswith("part ") for call in peer.calls)


def test_second_receiver_cannot_overlap(tmp_path):
    with transport.lock(tmp_path):
        with pytest.raises(OSError):
            with transport.lock(tmp_path):
                pytest.fail("concurrent process acquired the lock")


def test_complete_directory_symlink_refused(exported, tmp_path):
    root, ident, _ = exported
    store = tmp_path / "store"
    store.mkdir()
    (store / "completed").symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError):
        transport.pull(store, Peer(root))


def test_changed_manifest_is_not_silently_replaced(exported, tmp_path):
    root, ident, doc = exported
    store = tmp_path / "store"
    transport.pull(store, Peer(root))
    doc["untrusted_extra"] = "changed"
    (root / ident / "manifest.json").write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="immutable"):
        transport.pull(store, Peer(root))


def test_incomplete_exports_not_listed(exported):
    root, ident, _ = exported
    (root / "20260101T000000Z").mkdir()
    (root / ".incoming-secret").mkdir()
    out = io.BytesIO()
    transport.serve(root, "list", out)
    assert json.loads(out.getvalue()) == [ident]


def test_stale_copy_received_but_not_reported_fresh(exported, tmp_path):
    root, ident, doc = exported
    old = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y%m%dT%H%M%SZ")
    (root / ident).rename(root / old)
    doc["backup_id"] = old
    doc["created_utc"] = datetime.strptime(old, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
    (root / old / "manifest.json").write_text(json.dumps(doc))
    result = transport.pull(tmp_path / "store", Peer(root))
    assert result["freshness"] == "fail"
    assert result["backup_age_seconds"] > 26 * 3600


def test_full_export_encrypt_receive_and_decrypt(tmp_path):
    import subprocess
    import sys
    import tarfile
    ident = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dump = tmp_path / ("sora_earth_" + ident + ".dump")
    plaintext = b"synthetic database fixture only\x00\xff\n"
    dump.write_bytes(plaintext)
    Path(str(dump) + ".sha256").write_text(hashlib.sha256(plaintext).hexdigest() + "  ignored-path\n")
    identity, recipient = tmp_path / "test-identity.pem", tmp_path / "recipient.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072", "-out", str(identity)], check=True, capture_output=True)
    subprocess.run(["openssl", "pkey", "-in", str(identity), "-pubout", "-out", str(recipient)], check=True, capture_output=True)
    args = SimpleNamespace(dump=dump, root=tmp_path / "export", recipient=recipient,
                           reader_group=None, crypto_python=sys.executable)
    assert transport.export(args)["status"] == "exported"
    assert transport.export(args)["status"] == "already_exported"
    store = tmp_path / "receiver"
    assert transport.pull(store, Peer(args.root))["new_backups"] == [ident]
    archive = tmp_path / "restored.tar"
    library = Path(__file__).parents[1] / "scripts/backup_crypt.sh"
    import os
    subprocess.run(["bash", "-c", 'set -e; source "$1"; backup_decrypt "$2" "$3" "$4"',
                    "test-restore", str(library), str(store / "completed" / ident / "payload"), str(identity), str(archive)],
                   check=True, env={**os.environ, "BACKUP_PYTHON": sys.executable}, capture_output=True)
    with tarfile.open(archive) as restored:
        assert set(restored.getnames()) == {"dump", "metadata.json"}
        assert restored.extractfile("dump").read() == plaintext
        metadata = json.load(restored.extractfile("metadata.json"))
    assert metadata["dump_sha256"] == hashlib.sha256(plaintext).hexdigest()
    assert not list(args.root.glob(".incoming-*"))
    recovery = SimpleNamespace(backup=store / "completed" / ident, identity=identity,
                               output=tmp_path / "safe-restored", crypto_python=sys.executable)
    assert transport.restore_files(recovery)["database_restore"] == "not_performed"
    assert (recovery.output / "dump").read_bytes() == plaintext
    with pytest.raises(ValueError, match="exists"):
        transport.restore_files(recovery)
    dump.write_bytes(b"changed under the same name")
    Path(str(dump) + ".sha256").write_text(hashlib.sha256(dump.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="immutable_source"):
        transport.export(args)


def test_export_rejects_source_checksum_mismatch(tmp_path):
    import sys
    ident = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dump = tmp_path / ("sora_earth_" + ident + ".dump")
    dump.write_bytes(b"fixture")
    Path(str(dump) + ".sha256").write_text("0" * 64)
    recipient = tmp_path / "public.pem"
    recipient.write_text("not reached")
    args = SimpleNamespace(dump=dump, root=tmp_path / "export", recipient=recipient,
                           reader_group=None, crypto_python=sys.executable)
    with pytest.raises(ValueError, match="checksum"):
        transport.export(args)
    assert not (args.root / ident).exists()


def test_key_helpers_no_overwrite_and_distinct_copy_check(tmp_path):
    import subprocess
    import shutil
    scripts = Path(__file__).parents[1] / "scripts"
    key_dir = tmp_path / "mac-fixture"
    create = ["bash", str(scripts / "prepare_backup_recovery_key.sh"), str(key_dir)]
    subprocess.run(create, check=True, capture_output=True)
    original = (key_dir / "recovery.pem").read_bytes()
    assert subprocess.run(create, capture_output=True).returncode != 0
    assert (key_dir / "recovery.pem").read_bytes() == original
    verify_cmd = ["bash", str(scripts / "verify_backup_recovery_key.sh"), str(key_dir / "recovery.pem")]
    assert subprocess.run(verify_cmd + [str(key_dir / "recovery.pem")], capture_output=True).returncode != 0
    copy_file = tmp_path / "external-device-fixture.pem"
    shutil.copyfile(key_dir / "recovery.pem", copy_file)
    checked = subprocess.run(verify_cmd + [str(copy_file)], check=True, capture_output=True, text=True)
    assert "RECOVERY_KEY_COPIES_MATCH" in checked.stdout
    assert "PRIVATE KEY" not in checked.stdout
