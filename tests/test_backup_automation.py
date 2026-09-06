"""The backup pipeline's contracts, exercised against a local object store.

The parts that need PostgreSQL are marked; everything else -- encryption, the
completion contract, retention arithmetic and the restore guards -- is testable
without one, and those are the parts where a mistake is silent. A backup that
cannot be decrypted, or a partial upload that looks complete, is discovered
during an incident.
"""
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
FAKE_S3 = REPO / "tests" / "fakes" / "fake_s3"


def _executable_lines(path: Path) -> str:
    """A shell script with the lines that only talk stripped out.

    Ordering and presence assertions below search for SQL fragments by position.
    Comments explain the ordering using the same words, and the scripts print
    the very commands an operator should run by hand when something fails -- so
    both would otherwise decide the outcome of a test about what the code does.
    Two assertions in this file have already passed against prose while the code
    was wrong.
    """
    kept = []
    for line in path.read_text().splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith(("echo ", "echo>", 'echo "', "printf ")):
            continue
        kept.append(line)
    return "\n".join(kept)


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
    # errors="replace": a deliberately corrupted file is echoed back in an error
    # message, and the harness must not fail on the bytes it went looking for.
    return subprocess.run(["bash", "-c", textwrap.dedent(snippet)],
                          capture_output=True, text=True, errors="replace",
                          env=env, cwd=cwd)


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
    (d / "payload.hdr").write_text("sora-backup-envelope/1\naes-256-cbc/hmac-sha256/rsa-oaep-sha256\n")
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


def _run_backup_with_hook(tmp_path, *, hold_lock, env_extra=None):
    """One backup_run.sh invocation, with the alert hook recording what it got."""
    runtime = tmp_path / "rt"
    runtime.mkdir()
    runtime.chmod(0o700)
    hook = tmp_path / "hook"
    hook.write_text("#!/bin/sh\necho \"$1\" >> \"$ALERTS\"\n")
    hook.chmod(0o755)
    alerts = tmp_path / "alerts"
    alerts.write_text("")

    env = dict(os.environ,
               BACKUP_RUNTIME_DIR=str(runtime),
               BACKUP_ALERT_HOOK=str(hook),
               ALERTS=str(alerts))
    env.update(env_extra or {})

    holder = None
    if hold_lock:
        # The holder has to be its own live process, and it has to stay alive.
        #
        # Putting `acquire_backup_lock` and the run in one `bash -c` looks
        # equivalent and is not: bash execs the last command of a -c script,
        # replacing the process that held the lock. Under the flock backend the
        # replacement's own `exec 9>"$lockfile"` then closes the last reference to
        # the locked file description, the lock evaporates, and the run proceeds as
        # though nothing held it. Measured on Linux: rc=0 in that shape, rc=75 with
        # a live holder, rc=75 with a trailing no-op that merely defeats the
        # exec optimisation.
        #
        # It passed on macOS for a reason unrelated to correctness: the directory
        # backend's lock is filesystem state, which does not care which process
        # holds it. This is why the test is written against a live holder rather
        # than against whichever backend the host happens to have.
        held = tmp_path / "held"
        holder = subprocess.Popen(
            ["bash", "-c",
             "source scripts/backup_lock.sh\n"
             "acquire_backup_lock 'backup-demo' >/dev/null 2>&1 || exit 1\n"
             'printf held > "$HELD_FLAG"\n'
             "sleep 120\n"],
            cwd=REPO, env=dict(env, HELD_FLAG=str(held)),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Wait for the lock to actually be held rather than sleeping and hoping.
        deadline = time.monotonic() + 20
        while not held.exists():
            if holder.poll() is not None:
                raise AssertionError("the holder exited without taking the lock")
            if time.monotonic() > deadline:
                holder.kill()
                raise AssertionError("the holder never reported taking the lock")
            time.sleep(0.05)

    try:
        result = bash("./scripts/backup_run.sh demo", env=env)
    finally:
        if holder is not None:
            holder.kill()
            holder.wait()
    return result, [ln for ln in alerts.read_text().split() if ln]


def test_contention_alerts_once_and_not_as_a_failure(tmp_path):
    """A benign overlap must not page anyone.

    The EXIT trap alerts on any non-zero status, and the skip path exits 75 -- so
    an operator got backup_skipped and then backup_failed "stage exited 75" for
    the same harmless event. Reproduced before the fix: the hook fired twice.
    That is exactly the outcome the 75 branch exists to prevent, and there was no
    test holding it, which is how it shipped.
    """
    result, alerts = _run_backup_with_hook(tmp_path, hold_lock=True)

    assert result.returncode == 75, result.stderr
    assert alerts == ["backup_skipped"], f"alerts fired: {alerts}"
    assert "backup_failed" not in result.stderr


def test_a_real_failure_still_alerts(tmp_path, store):
    """The counterpart: exempting the skip must not have muted genuine faults.

    The store is configured so the run gets past store_ready and fails on the one
    thing this test is about -- an unreadable recipient key. Without it the failing
    stage differed by environment (a missing bucket in CI, the key locally), and a
    test that passes for a different reason on each machine is not measuring what
    its name says.
    """
    result, alerts = _run_backup_with_hook(
        tmp_path, hold_lock=False,
        env_extra=dict(store.env, BACKUP_RECIPIENT_KEY=str(tmp_path / "absent.pem")))

    assert result.returncode != 0
    assert alerts == ["backup_failed"], f"alerts fired: {alerts}"
    assert "readable public key" in result.stderr, result.stderr


def test_a_sourced_library_does_not_set_options_in_its_caller():
    """Shell options are global to the process, so a sourced file sets them for
    whoever sourced it.

    backup_lock.sh did, and errexit in the caller turned `acquire_backup_lock`
    returning 75 -- someone else holds it -- into an immediate exit, past the
    branch that reports the skip. Every executable here sets its own options
    before sourcing anything, so the libraries have nothing to add.
    """
    sourced = {"pg_lib.sh", "backup_crypt.sh", "backup_store.sh", "backup_lock.sh"}
    for name in sorted(sourced):
        code = _executable_lines(SCRIPTS / name)
        offenders = [ln for ln in code.splitlines() if ln.strip().startswith("set -")]
        assert not offenders, f"{name} sets shell options in its caller: {offenders}"

    # And the converse, for the scripts that source them: each must set its own
    # options, and set them before sourcing -- otherwise removing them from the
    # libraries takes errexit away with no replacement. Scoped to the sourcing
    # scripts; the rest of scripts/ predates this pipeline.
    sourcing = [p for p in sorted(SCRIPTS.glob("*.sh"))
                if p.name not in sourced and "source scripts/" in p.read_text()]
    assert sourcing, "no sourcing scripts found; this test is checking nothing"
    for script in sourcing:
        lines = script.read_text().splitlines()
        first_set = next((i for i, ln in enumerate(lines) if ln.startswith("set -")), None)
        first_source = next(i for i, ln in enumerate(lines) if ln.startswith("source scripts/"))
        assert first_set is not None, f"{script.name} sets no options of its own"
        assert first_set < first_source, \
            f"{script.name} sources a library before setting its own options"


def test_no_backup_script_needs_a_tool_debian_does_not_ship():
    """xxd comes with vim, not with coreutils.

    It is absent from python:3.11-slim, and no image in this repo ships scripts/
    at all -- these run on the host, whose toolchain the repo does not pin. A
    hex encoder that is missing is a backup that does not happen, at 03:00, on
    the night somebody reinstalled without vim. od is in coreutils.

    Passes on this machine either way, which is the point: the local host has
    xxd, so the round-trip tests could not have caught this.
    """
    for script in sorted(SCRIPTS.glob("backup_*.sh")):
        code = _executable_lines(script)
        assert "xxd" not in code, f"{script.name} depends on xxd; use od -An -tx1 -v"


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


# ------------------------------------------------------- envelope integrity


@pytest.fixture
def sealed(tmp_path, keypair):
    """One encrypted payload, ready to be damaged in specific ways."""
    plain = tmp_path / "plain"
    plain.write_bytes(b"KNOWN-PLAINTEXT-MARKER" * 2000)
    bash(f"""
        source scripts/backup_crypt.sh
        backup_encrypt {plain} {keypair.recipient} {tmp_path}/s
    """)
    return types_ns(dir=tmp_path, prefix=tmp_path / "s", plain=plain)


def attempt_decrypt(sealed, keypair, out="out"):
    return bash(f"""
        source scripts/backup_crypt.sh
        backup_decrypt {sealed.prefix} {keypair.identity} {sealed.dir}/{out}
    """)


@pytest.mark.parametrize("part", ["enc", "hdr", "mac", "key"])
def test_tampering_with_any_part_is_refused(sealed, keypair, part):
    """Signing only the ciphertext would leave the parameters describing it
    unprotected, which is how downgrade attacks on sound constructions work."""
    target = Path(f"{sealed.prefix}.{part}")
    data = target.read_bytes()
    target.write_bytes(data[:20] + bytes([data[20] ^ 0xFF]) + data[21:])

    result = attempt_decrypt(sealed, keypair, out=f"out-{part}")

    assert result.returncode != 0, f"a damaged .{part} was accepted"
    assert not (sealed.dir / f"out-{part}").exists(), \
        f"plaintext was written despite a damaged .{part}"


def test_an_unknown_envelope_version_is_refused(sealed, keypair):
    Path(f"{sealed.prefix}.hdr").write_text("sora-backup-envelope/99\nwhatever\n")

    result = attempt_decrypt(sealed, keypair, out="future")

    assert result.returncode != 0
    assert "unknown envelope version" in result.stderr
    assert not (sealed.dir / "future").exists()


def test_the_same_plaintext_encrypts_differently_each_time(tmp_path, keypair):
    """A fresh key and IV per backup: identical dumps must not be linkable."""
    plain = tmp_path / "same"
    plain.write_bytes(b"identical contents" * 1000)
    for run in ("a", "b"):
        bash(f"""
            source scripts/backup_crypt.sh
            backup_encrypt {plain} {keypair.recipient} {tmp_path}/{run}
        """)

    assert (tmp_path / "a.enc").read_bytes() != (tmp_path / "b.enc").read_bytes()
    assert (tmp_path / "a.key").read_bytes() != (tmp_path / "b.key").read_bytes()


def test_the_ciphertext_does_not_contain_the_plaintext(sealed):
    assert b"KNOWN-PLAINTEXT-MARKER" not in Path(f"{sealed.prefix}.enc").read_bytes()


def _open_material(sealed, keypair) -> bytes:
    """The 80 sealed bytes, as the holder of the private key would see them."""
    out = subprocess.run(
        ["openssl", "pkeyutl", "-decrypt", "-inkey", str(keypair.identity),
         "-pkeyopt", "rsa_padding_mode:oaep", "-pkeyopt", "rsa_oaep_md:sha256",
         "-in", f"{sealed.prefix}.key"],
        capture_output=True, check=True,
    )
    return out.stdout


def test_the_keys_are_independent_not_one_key_reused(sealed, keypair):
    """Using one key for confidentiality and authentication weakens both.

    Asserted against the envelope rather than against the source: this used to
    match the dd offsets in the shell, and stopped meaning anything the moment
    the slicing moved into the helper. What matters is which bytes end up where,
    so that is what gets checked -- by rebuilding both outputs from the slices
    the documentation names.
    """
    material = _open_material(sealed, keypair)
    assert len(material) == 80
    enc_key, mac_key, iv = material[0:32], material[32:64], material[64:80]
    assert enc_key != mac_key
    assert len({enc_key, mac_key, iv}) == 3

    # [0:32] is the AES key and [64:80] the IV: re-encrypting with them
    # reproduces the payload exactly.
    cipher = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).encryptor()
    padder = sym_padding.PKCS7(128).padder()
    redone = cipher.update(padder.update(Path(sealed.plain).read_bytes()) + padder.finalize())
    redone += cipher.finalize()
    assert redone == Path(f"{sealed.prefix}.enc").read_bytes()

    # [32:64] is the MAC key, over header || ciphertext.
    body = Path(f"{sealed.prefix}.hdr").read_bytes() + Path(f"{sealed.prefix}.enc").read_bytes()
    assert hmac.new(mac_key, body, hashlib.sha256).hexdigest() == \
        Path(f"{sealed.prefix}.mac").read_text().split()[-1].strip()


def test_the_envelope_is_the_one_openssl_produced(tmp_path):
    """The helper replaced two openssl invocations. If the bytes had changed, the
    version field would have had to change with them -- so the bytes are checked
    rather than the claim.

    Encryption is deterministic given the key and IV, which is what makes a
    byte-for-byte comparison possible here and is also why the IV is fresh per
    backup rather than fixed.
    """
    material = tmp_path / "mat"
    material.write_bytes(os.urandom(80))
    # Not a multiple of the block size, so padding is actually exercised.
    plain = tmp_path / "plain"
    plain.write_bytes(os.urandom(4099))
    hex_of = lambda b: b.hex()  # noqa: E731 - a name for the openssl argument form
    raw = material.read_bytes()

    helper_out = tmp_path / "helper.enc"
    subprocess.run(
        [sys.executable, str(SCRIPTS / "backup_crypt.py"), "encrypt",
         "--material", str(material), "--in", str(plain), "--out", str(helper_out)],
        check=True, capture_output=True,
    )
    openssl_out = tmp_path / "openssl.enc"
    subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-K", hex_of(raw[0:32]), "-iv", hex_of(raw[64:80]),
         "-in", str(plain), "-out", str(openssl_out)],
        check=True, capture_output=True,
    )
    assert helper_out.read_bytes() == openssl_out.read_bytes()

    mac = subprocess.run(
        [sys.executable, str(SCRIPTS / "backup_crypt.py"), "mac",
         "--material", str(material), str(plain)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    openssl_mac = subprocess.run(
        ["openssl", "dgst", "-sha256", "-mac", "HMAC",
         "-macopt", f"hexkey:{hex_of(raw[32:64])}", str(plain)],
        check=True, capture_output=True, text=True,
    ).stdout.split()[-1].strip()
    assert mac == openssl_mac


def test_no_key_material_reaches_a_command_line():
    """/proc/<pid>/cmdline is mode 444.

    Measured on Linux: while root ran `openssl enc -K <hex>`, an unprivileged
    user read the AES key out of it. openssl offers no way to hand `enc` a raw
    key, or `dgst` a MAC key, off the argument list -- every alternative feeds a
    passphrase through a KDF, which is a different envelope. So the symmetric
    half moved to backup_crypt.py, which reads the material from a 0600 file.
    A path in argv is not a secret.
    """
    code = _executable_lines(SCRIPTS / "backup_crypt.sh")
    assert "-macopt" not in code, "a MAC key is being passed on a command line"
    assert "-K " not in code, "a raw key is being passed on a command line"
    assert "openssl enc" not in code, "openssl enc cannot take a key off argv"
    # The RSA half is fine: -inkey names a file.
    assert "-inkey" in code


# --------------------------------------------------------------- lock owner


def test_the_lock_does_not_live_where_anyone_can_write():
    """The metadata approach this replaces could not have worked.

    The lock used to sit at a predictable path under /tmp and record host, pid,
    start time and a token so that a stale entry could be told from a live one.
    But any local user can create that path first, and having created it they
    choose what the metadata says -- so the fields being present proved nothing.
    Pre-creating the directory with the pid of some long-lived process would have
    stopped every backup, indefinitely and quietly.

    What makes the metadata trustworthy is the directory: 0700 and ours, so
    nothing else could have written it. The behavioural coverage is in
    tests/test_backup_lock.sh, run by test_the_lock_suite_passes below.
    """
    runner = (SCRIPTS / "backup_run.sh").read_text()
    assert "source scripts/backup_lock.sh" in runner, \
        "the runner no longer delegates to the lock library"

    code = _executable_lines(SCRIPTS / "backup_run.sh")
    assert "/tmp" not in code, "a /tmp path came back into the runner"

    lib = _executable_lines(SCRIPTS / "backup_lock.sh")
    assert "validate_runtime_dir" in lib
    assert "8#077" in lib, "the mode check that keeps others out is gone"
    # A library that sets shell options sets them in whoever sourced it, and
    # errexit there turns "someone else holds the lock" into a silent exit.
    assert not any(
        line.strip().startswith("set -e") for line in lib.splitlines()
    ), "the sourced library sets errexit in its caller"


def test_release_only_removes_a_directory_this_process_took():
    lib = _executable_lines(SCRIPTS / "backup_lock.sh")
    release = lib[lib.index("release_backup_lock()"):]
    assert 'BACKUP_LOCK_BACKEND:-}" = "mkdir"' in release, \
        "release must do nothing under flock, where the kernel owns the lock"
    assert 'BACKUP_LOCK_HELD_DIR:-}" ]' in release, \
        "release must only remove a directory this process recorded taking"


def test_the_lock_suite_passes():
    """The lock's behaviour, not its text. On Linux this covers both backends;
    on a host without flock the flock cases report themselves as skipped."""
    suite = REPO / "tests" / "test_backup_lock.sh"
    result = subprocess.run(
        ["bash", str(suite)], capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert " 0 failed" in result.stdout, result.stdout


# ------------------------------------------------------- restore atomicity


def test_the_restore_is_atomic_and_fails_closed():
    """A non-zero exit is not a rollback.

    pg_restore reports failure but does not undo the statements it already
    applied, so without a transaction the target is left partially populated --
    worse than empty, because it looks usable. Measured on PostgreSQL 16 with a
    dump whose GRANT names a role the target cluster lacks:

        without --single-transaction:  exit 1, and 50 rows sitting in the table
        with it:                       exit 1, and the relation does not exist
    """
    text = (SCRIPTS / "backup_restore.sh").read_text()
    assert "--single-transaction" in text
    assert "--exit-on-error" in text


def test_the_target_is_not_dropped_before_a_good_restore_exists():
    """Dropping first destroys what was there before the replacement is known
    to work. The failing restore above would have left nothing to go back to."""
    code = _executable_lines(SCRIPTS / "backup_restore.sh")

    staging_created = code.index('CREATE DATABASE \\"$STAGING\\"')
    restore_runs = code.index("--single-transaction")
    # WITH (FORCE) pins this to the promotion. A plain drop of the target also
    # appears in the guidance the script prints when promotion fails.
    target_dropped = code.index('DROP DATABASE IF EXISTS \\"$TARGET\\" WITH (FORCE)')
    promoted = code.index("RENAME TO")

    assert staging_created < restore_runs, "staging must exist before the restore"
    assert restore_runs < target_dropped, "the target is dropped before the restore proves out"
    assert target_dropped < promoted, "the rename must follow the drop"


def test_a_failed_restore_discards_the_staging_database():
    text = (SCRIPTS / "backup_restore.sh").read_text()
    assert "drop_staging" in text
    assert "trap 'rm -rf \"$WORK\"; drop_staging' EXIT" in text, \
        "staging must be dropped on every exit path, not just the happy one"


# ------------------------------------------------------- the off-site drill
#
# These run the real scripts. `pg_lib.sh` executes the PostgreSQL tools from
# PATH when PG_CONTAINER is empty, so tests/fakes/pg stands in for the server
# and `backup_run.sh` and `backup_offsite_drill.sh` are executed whole rather
# than described.

FAKE_PG = REPO / "tests" / "fakes" / "pg"


def _store_env(store, keypair, **extra):
    env = dict(store.env)
    env.update(
        PATH=f"{FAKE_PG}:{env['PATH']}",
        PG_CONTAINER="",
        PGUSER="sora",
        BACKUP_RECIPIENT_KEY=str(keypair.recipient),
        BACKUP_IDENTITY_KEY=str(keypair.identity),
    )
    env.update(extra)
    return env


def _make_backup(store, keypair, **extra):
    """Run the real backup, against the fake store and the fake server."""
    env = _store_env(store, keypair, **extra)
    done = bash("./scripts/backup_run.sh sora_test", env=env)
    return done, env


@pytest.fixture()
def a_backup(store, keypair):
    done, env = _make_backup(store, keypair)
    assert done.returncode == 0, done.stdout + done.stderr
    ids = sorted(p.name for p in store.prefix.iterdir() if p.is_dir())
    assert len(ids) == 1, ids
    return types_ns(id=ids[0], env=env, dir=store.prefix / ids[0])


def test_the_manifest_is_published_last_and_never_first(store, keypair):
    """The completion contract, asserted on the order of the uploads.

    S3 has no atomic rename, so "the set exists" cannot be a rename. It is the
    manifest, and the manifest is only true if everything it describes is
    already there. An upload order that put it first would make every partial
    upload look like a finished backup -- discovered during a restore.
    """
    log = store.root / "upload.log"
    done, _ = _make_backup(store, keypair, FAKE_S3_LOG=str(log))
    assert done.returncode == 0, done.stdout + done.stderr

    # argv verbatim: "s3 cp <local> s3://bucket/key". Uploads only -- a
    # download has the s3:// on the other side.
    uploads = []
    for line in log.read_text().splitlines():
        parts = line.split()
        if parts[:2] == ["s3", "cp"] and parts[-1].startswith("s3://"):
            uploads.append(parts[-1])
    assert uploads, log.read_text()
    manifests = [i for i, key in enumerate(uploads) if key.endswith("manifest.json")]

    assert manifests, f"no manifest was uploaded: {uploads}"
    assert manifests[0] == len(uploads) - 1, (
        f"the manifest is not the last upload: {uploads}"
    )
    assert manifests[0] != 0, "the manifest was uploaded first"


def test_a_newer_incomplete_set_is_skipped_for_an_older_complete_one(store, keypair, a_backup):
    """Freshness is not the selector; completeness is.

    A backup interrupted after the payload and before the manifest leaves a
    directory that is newer than every good one. Choosing by name or by time
    would pick it.
    """
    newer = store.prefix / "99999999T999999Z-deadbeef"
    newer.mkdir()
    (newer / "payload.enc").write_bytes(b"half an upload")

    env = _store_env(store, keypair)
    listed = bash("source scripts/backup_store.sh && store_list_backups", env=env)

    assert a_backup.id in listed.stdout
    assert "99999999T999999Z-deadbeef" not in listed.stdout, (
        "an unfinished upload was offered as a restorable backup"
    )

    drill = bash("./scripts/backup_offsite_drill.sh", env=env)
    assert f"chosen         : {a_backup.id}" in drill.stdout, drill.stdout


def test_the_drill_restores_the_off_site_copy_and_matches_the_fingerprint(store, keypair, a_backup):
    """The whole path, from the store: download, verify, decrypt, restore, compare."""
    drill = bash("./scripts/backup_offsite_drill.sh", env=a_backup.env)

    assert drill.returncode == 0, drill.stdout + drill.stderr
    for step in ("all encryption parts present", "downloaded bytes",
                 "ciphertext sha256", "dump sha256",
                 "table of contents", "fingerprint matches"):
        assert step in drill.stdout, f"{step!r} missing from:\n{drill.stdout}"
    assert "SKIP" not in drill.stdout, drill.stdout


def test_a_truncated_download_is_refused_before_decryption(store, keypair, a_backup):
    """The bytes that arrived are checked against the manifest, not assumed.

    **Where** it stops is the assertion, not merely that it stops. With the
    size and hash checks removed the drill still failed -- one step later, in
    `backup_crypt.sh`, whose message also contains "refusing to decrypt". The
    first version of this test matched that substring and passed with the
    checks gone. So it now asserts the decrypt stage was never entered.
    """
    payload = a_backup.dir / "payload.enc"
    payload.write_bytes(payload.read_bytes()[:-64])

    drill = bash("./scripts/backup_offsite_drill.sh", env=a_backup.env)

    assert drill.returncode != 0
    assert "FAIL  downloaded bytes" in drill.stdout, drill.stdout
    assert "stopping before decryption" in drill.stderr, drill.stdout + drill.stderr
    assert "5. decrypt" not in drill.stdout, (
        "the drill went on to decrypt a payload it had already found wrong:\n"
        + drill.stdout
    )


def test_a_set_missing_a_part_is_refused_before_anything_is_downloaded(store, keypair, a_backup):
    """A manifest names a set. It does not prove the set arrived."""
    (a_backup.dir / "payload.key").unlink()

    drill = bash("./scripts/backup_offsite_drill.sh", env=a_backup.env)

    assert drill.returncode != 0
    assert "the set is incomplete: payload.key is missing" in drill.stderr, drill.stderr


def test_the_wrong_identity_key_cannot_decrypt(store, keypair, a_backup, tmp_path):
    """A different private key must fail loudly, not produce plausible bytes."""
    other = tmp_path / "other.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "RSA",
                    "-pkeyopt", "rsa_keygen_bits:3072", "-out", str(other)],
                   check=True, capture_output=True)
    env = dict(a_backup.env, BACKUP_IDENTITY_KEY=str(other))

    drill = bash("./scripts/backup_offsite_drill.sh", env=env)

    assert drill.returncode != 0
    combined = drill.stdout + drill.stderr
    assert "fingerprint matches" not in combined, combined


def test_an_unreadable_archive_fails_at_the_table_of_contents(store, keypair, a_backup):
    """`pg_restore --list` before any database is touched.

    A payload that decrypts but is not an archive must be caught before a
    database is created for it, or the drill leaves a mess behind to explain a
    failure it already knew about.
    """
    env = dict(a_backup.env, FAKE_PG_LIST_FAIL="1")

    drill = bash("./scripts/backup_offsite_drill.sh", env=env)

    assert drill.returncode != 0
    assert "pg_restore --list refused the archive" in drill.stdout, drill.stdout


def test_a_client_failure_is_surfaced_and_not_swallowed(store, keypair, a_backup):
    """A store that refuses must stop the drill, not produce a green run."""
    env = dict(a_backup.env, FAKE_S3_FAIL_ON="payload.enc")

    drill = bash("./scripts/backup_offsite_drill.sh", env=env)

    assert drill.returncode != 0
    assert "fingerprint matches" not in drill.stdout, drill.stdout


def test_the_drill_drops_its_database_on_every_exit(store, keypair, a_backup):
    """Success and failure both. A leftover database is the next operator's problem."""
    log = store.root / "pg.log"

    for extra in ({}, {"FAKE_PG_RESTORE_FAIL": "1"}):
        log.write_text("")
        env = dict(a_backup.env, FAKE_PG_LOG=str(log), **extra)
        bash("./scripts/backup_offsite_drill.sh", env=env)

        calls = log.read_text()
        created = [l for l in calls.splitlines() if "CREATE DATABASE" in l]
        dropped = [l for l in calls.splitlines() if "DROP DATABASE" in l]
        assert created, f"no database was created, so the test proves nothing:\n{calls}"
        assert any("WITH (FORCE)" in l for l in dropped), (
            f"the drill did not drop its database ({extra or 'success path'}):\n{calls}"
        )


def test_a_retry_after_a_failed_upload_produces_a_usable_backup(store, keypair):
    """Idempotent in the way that matters: the second run is a whole backup.

    The first attempt dies after some parts are uploaded. Nothing cleans up --
    deliberately, sweeping leftovers is a separate unhurried job -- so what
    must hold is that the retry produces a *complete* set and that the debris
    is never offered as one.
    """
    failed, _ = _make_backup(store, keypair, FAKE_S3_FAIL_ON="manifest.json")
    assert failed.returncode != 0, failed.stdout

    done, env = _make_backup(store, keypair)
    assert done.returncode == 0, done.stdout + done.stderr

    listed = bash("source scripts/backup_store.sh && store_list_backups", env=env)
    complete = [i for i in listed.stdout.split() if i]
    assert len(complete) == 1, f"expected one completed set, got {complete}"

    drill = bash("./scripts/backup_offsite_drill.sh", env=env)
    assert drill.returncode == 0, drill.stdout + drill.stderr


def test_the_endpoint_url_reaches_the_client(store, keypair):
    """Provider-neutrality is this one argument. If it is dropped, every call
    goes to AWS instead of the configured endpoint -- with credentials."""
    log = store.root / "endpoint.log"
    env = _store_env(store, keypair,
                     BACKUP_S3_ENDPOINT="https://s3.example.invalid",
                     FAKE_S3_LOG=str(log))

    bash("source scripts/backup_store.sh && store_exists 'nothing/at/all'", env=env)

    assert log.exists(), "the client was never called"
    assert "--endpoint-url https://s3.example.invalid" in log.read_text(), log.read_text()


# ------------------------------------------------------------ the IAM example


IAM_DOC = REPO / "docs" / "BACKUP_S3_IAM.md"


def _iam_policies():
    import re

    blocks = re.findall(r"```json\n(.*?)```", IAM_DOC.read_text(), re.S)
    return [json.loads(b) for b in blocks]


def test_the_iam_example_is_three_valid_least_privilege_policies():
    """The document makes claims. They are cheap to check, so they are checked.

    An IAM example nobody validates is a snippet someone pastes into a console
    at the moment they are least able to review it.
    """
    policies = _iam_policies()
    assert len(policies) == 3, f"expected writer/restorer/janitor, got {len(policies)}"

    for policy in policies:
        for statement in policy["Statement"]:
            actions = statement["Action"]
            assert all(not a.endswith(":*") for a in actions), f"wildcard action: {actions}"
            assert "*" not in actions, f"a bare wildcard action: {actions}"
            resource = json.dumps(statement["Resource"])
            assert "BUCKET-NAME" in resource, f"unscoped Resource: {resource}"


def test_the_writer_cannot_delete_and_the_restorer_cannot_write():
    """The division is the safety. A single identity with all three verbs means
    a compromised backup host can erase the backups it just wrote -- which is
    the failure an off-site copy exists to survive."""
    writer, restorer, janitor = _iam_policies()

    def verbs(policy):
        return {a for s in policy["Statement"] for a in s["Action"]}

    assert "s3:PutObject" in verbs(writer)
    assert not {"s3:DeleteObject", "s3:ListBucket"} & verbs(writer), verbs(writer)

    assert "s3:GetObject" in verbs(restorer)
    assert not {"s3:PutObject", "s3:DeleteObject"} & verbs(restorer), verbs(restorer)

    assert "s3:DeleteObject" in verbs(janitor)
    assert "s3:PutObject" not in verbs(janitor), verbs(janitor)


def test_the_document_contains_no_credential_shaped_string():
    """Placeholders only. A real key id in a repository is a real key id."""
    import re

    text = IAM_DOC.read_text()
    assert not re.search(r"\bAKIA[0-9A-Z]{16}\b", text), "an AWS access key id"
    assert not re.search(r"\b[0-9]{12}\b", text), "what looks like an AWS account id"
    assert "BUCKET-NAME" in text and "PREFIX" in text
