"""The backup pipeline's contracts, exercised against a local object store.

The parts that need PostgreSQL are marked; everything else -- encryption, the
completion contract, retention arithmetic and the restore guards -- is testable
without one, and those are the parts where a mistake is silent. A backup that
cannot be decrypted, or a partial upload that looks complete, is discovered
during an incident.
"""
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
FAKE_S3 = REPO / "tests" / "fakes" / "fake_s3"


@pytest.fixture
def store(tmp_path):
    """A bucket in a directory, and the environment that points the scripts at it."""
    root = tmp_path / "s3"
    root.mkdir()
    env = dict(os.environ)
    env.update(
        BACKUP_S3_CLIENT=str(FAKE_S3),
        BACKUP_S3_BUCKET="backups",
        BACKUP_S3_PREFIX="sora",
        FAKE_S3_ROOT=str(root),
        PATH=f"{FAKE_S3.parent}:{env['PATH']}",
    )
    return types_ns(root=root, env=env, prefix=root / "backups" / "sora")


def types_ns(**kw):
    import types
    return types.SimpleNamespace(**kw)


@pytest.fixture(scope="session")
def keypair(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("keys")
    identity = tmp_path / "identity.pem"
    recipient = tmp_path / "recipient.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "RSA",
                    "-pkeyopt", "rsa_keygen_bits:3072", "-out", str(identity)],
                   check=True, capture_output=True)
    subprocess.run(["openssl", "pkey", "-in", str(identity), "-pubout",
                    "-out", str(recipient)], check=True, capture_output=True)
    return types_ns(identity=identity, recipient=recipient)


def bash(snippet, env=None, cwd=REPO):
    return subprocess.run(["bash", "-c", textwrap.dedent(snippet)],
                          capture_output=True, text=True, env=env, cwd=cwd)


# ----------------------------------------------------------------- encryption


def test_the_backup_host_cannot_read_its_own_backups(tmp_path, keypair):
    """A passphrase would fail this: whatever encrypts would also decrypt."""
    plain = tmp_path / "dump"
    plain.write_bytes(os.urandom(50_000))

    result = bash(f"""
        source scripts/backup_crypt.sh
        backup_encrypt {plain} {keypair.recipient} {tmp_path}/sealed
        backup_decrypt {tmp_path}/sealed {keypair.recipient} {tmp_path}/stolen
    """)

    assert result.returncode != 0
    assert not (tmp_path / "stolen").exists()


def test_a_tampered_payload_is_refused_before_decryption(tmp_path, keypair):
    plain = tmp_path / "dump"
    plain.write_bytes(b"the original contents" * 500)
    bash(f"""
        source scripts/backup_crypt.sh
        backup_encrypt {plain} {keypair.recipient} {tmp_path}/sealed
    """)

    sealed = (tmp_path / "sealed.enc").read_bytes()
    (tmp_path / "sealed.enc").write_bytes(sealed[:100] + b"\x00" + sealed[101:])

    result = bash(f"""
        source scripts/backup_crypt.sh
        backup_decrypt {tmp_path}/sealed {keypair.identity} {tmp_path}/out
    """)

    assert result.returncode != 0
    assert "failed authentication" in result.stderr
    assert not (tmp_path / "out").exists(), "plaintext was written from a tampered payload"


def test_a_round_trip_reproduces_the_dump_exactly(tmp_path, keypair):
    plain = tmp_path / "dump"
    original = os.urandom(200_000)
    plain.write_bytes(original)

    bash(f"""
        source scripts/backup_crypt.sh
        backup_encrypt {plain} {keypair.recipient} {tmp_path}/sealed
        backup_decrypt {tmp_path}/sealed {keypair.identity} {tmp_path}/restored
    """)

    assert (tmp_path / "restored").read_bytes() == original


# --------------------------------------------------------- completion contract


def make_backup(store, backup_id, *, complete=True, sha="abc", size=10):
    """Place objects the way the pipeline does -- manifest last, or not at all."""
    d = store.prefix / backup_id
    d.mkdir(parents=True)
    (d / "payload.enc").write_bytes(b"x" * size)
    (d / "payload.mac").write_text("mac")
    (d / "payload.key").write_text("key")
    (d / "metadata.json").write_text(json.dumps({"backup_id": backup_id}))
    if complete:
        (d / "manifest.json").write_text(json.dumps(
            {"backup_id": backup_id, "payload_sha256": sha, "payload_bytes": size}))
    return d


def list_backups(store):
    result = bash("source scripts/backup_store.sh; store_list_backups", env=store.env)
    return [line for line in result.stdout.split() if line]


def test_a_backup_without_a_manifest_is_not_a_backup(store):
    """An interrupted upload leaves objects behind; none of them count."""
    make_backup(store, "20260101T000000Z-aaaa", complete=True)
    make_backup(store, "20260102T000000Z-bbbb", complete=False)

    assert list_backups(store) == ["20260101T000000Z-aaaa"]


def test_retention_never_considers_an_incomplete_upload(store):
    for day in range(1, 32):
        make_backup(store, f"202601{day:02d}T000000Z-cccc", complete=True)
    make_backup(store, "20260201T000000Z-partial", complete=False)

    env = dict(store.env, BACKUP_KEEP_ROLLING="3", BACKUP_KEEP_WEEKLY="0")
    result = bash("./scripts/backup_retention.sh", env=env)

    assert "20260201T000000Z-partial" not in result.stdout, \
        "an unfinished upload was treated as a backup"


# ------------------------------------------------------------------ retention


def test_the_dry_run_deletes_nothing(store):
    for day in range(1, 10):
        make_backup(store, f"202601{day:02d}T000000Z-dddd")
    before = sorted(p.name for p in store.prefix.iterdir())

    result = bash("./scripts/backup_retention.sh",
                  env=dict(store.env, BACKUP_KEEP_ROLLING="2", BACKUP_KEEP_WEEKLY="0"))

    assert "would delete" in result.stdout
    assert sorted(p.name for p in store.prefix.iterdir()) == before


def test_the_keep_set_is_the_newest_n(store):
    for day in range(1, 11):
        make_backup(store, f"202601{day:02d}T000000Z-eeee")

    bash("./scripts/backup_retention.sh",
         env=dict(store.env, BACKUP_KEEP_ROLLING="3", BACKUP_KEEP_WEEKLY="0",
                  BACKUP_RETENTION_APPLY="1"))

    remaining = sorted(p.name for p in store.prefix.iterdir())
    assert remaining == ["20260108T000000Z-eeee",
                         "20260109T000000Z-eeee",
                         "20260110T000000Z-eeee"]


def test_the_newest_backup_is_never_deleted(store):
    make_backup(store, "20260101T000000Z-ffff")

    bash("./scripts/backup_retention.sh",
         env=dict(store.env, BACKUP_KEEP_ROLLING="0", BACKUP_KEEP_WEEKLY="0",
                  BACKUP_RETENTION_APPLY="1"))

    assert (store.prefix / "20260101T000000Z-ffff").exists(), \
        "retention removed the only backup there was"


def test_a_weekly_pick_survives_the_rolling_window(store):
    """Otherwise the weekly slot is decorative: it would age out with the rest."""
    for week in range(1, 9):
        for day in range(1, 8):
            make_backup(store, f"2026{week:02d}{day:02d}T000000Z-gggg")

    result = bash("./scripts/backup_retention.sh",
                  env=dict(store.env, BACKUP_KEEP_ROLLING="2", BACKUP_KEEP_WEEKLY="8"))

    kept = [l for l in result.stdout.splitlines() if "keep" in l]
    assert len(kept) > 2, "no backup was kept for the weekly window"


# ------------------------------------------------------------- restore guards


def test_restore_refuses_a_protected_database(store, keypair, tmp_path):
    make_backup(store, "20260101T000000Z-hhhh")
    env = dict(store.env,
               BACKUP_RESTORE_TARGET="sora_earth",
               BACKUP_IDENTITY_KEY=str(keypair.identity))

    result = bash("./scripts/backup_restore.sh 20260101T000000Z-hhhh", env=env)

    assert result.returncode == 3
    assert "protected database" in result.stderr


def test_restore_requires_an_explicit_target(store, keypair):
    env = dict(store.env, BACKUP_IDENTITY_KEY=str(keypair.identity))
    env.pop("BACKUP_RESTORE_TARGET", None)

    result = bash("./scripts/backup_restore.sh 20260101T000000Z-iiii", env=env)

    assert result.returncode == 2
    assert "must name the database" in result.stderr


def test_restore_refuses_a_backup_with_no_manifest(store, keypair):
    make_backup(store, "20260101T000000Z-jjjj", complete=False)
    env = dict(store.env,
               BACKUP_RESTORE_TARGET="sora_drill",
               BACKUP_IDENTITY_KEY=str(keypair.identity))

    result = bash("./scripts/backup_restore.sh 20260101T000000Z-jjjj", env=env)

    assert result.returncode != 0
    assert "not a completed backup" in result.stderr


def test_restore_refuses_a_payload_that_disagrees_with_its_manifest(store, keypair):
    """The manifest is the claim; the bytes are the evidence."""
    make_backup(store, "20260101T000000Z-kkkk", sha="0" * 64)
    env = dict(store.env,
               BACKUP_RESTORE_TARGET="sora_drill",
               BACKUP_IDENTITY_KEY=str(keypair.identity))

    result = bash("./scripts/backup_restore.sh 20260101T000000Z-kkkk", env=env)

    assert result.returncode != 0
    assert "does not match the manifest" in result.stderr


# --------------------------------------------------------------- housekeeping


def test_no_script_puts_a_credential_in_argv():
    """An argument list is readable by every process on the host."""
    for script in ("backup_run.sh", "backup_store.sh", "backup_restore.sh"):
        text = (SCRIPTS / script).read_text()
        assert "--secret-key" not in text
        assert "AWS_SECRET_ACCESS_KEY=$" not in text.replace('AWS_SECRET_ACCESS_KEY="$(<', "")


def test_every_script_parses():
    for script in SCRIPTS.glob("backup_*.sh"):
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, f"{script.name}: {result.stderr}"


def test_the_scripts_run_on_the_bash_the_platform_ships():
    """macOS ships bash 3.2: no mapfile, no associative arrays.

    A maintenance script that only parses on the deployment host is a script
    whose faults are found in production. This caught exactly that.
    """
    for script in sorted(SCRIPTS.glob("backup_*.sh")):
        result = subprocess.run(["/bin/bash", "-n", str(script)],
                                capture_output=True, text=True)
        assert result.returncode == 0, f"{script.name}: {result.stderr.strip()}"
        # Comments explain why these are avoided, so only code is inspected.
        code = "\n".join(
            line for line in script.read_text().splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "mapfile" not in code, f"{script.name} uses mapfile (bash 4+)"
        assert "declare -A" not in code, f"{script.name} uses an associative array (bash 4+)"
