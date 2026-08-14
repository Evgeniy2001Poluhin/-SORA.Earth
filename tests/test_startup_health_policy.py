"""The container does not report broken while it is loading correctly.

#50. `app/main.py` unpickles the models at import, and nothing answers on :8000
until that finishes -- so separating liveness from readiness does not help
here, which is worth stating because it is the conventional treatment and it
does not apply: during the load there is no server to ask either question of.

The only question is how long the grace period is, and it was guessed. Measured
on production 2026-08-14 the load takes 14.8 seconds against a 20-second
period; on the isolated stand where #50 was found the same image took past
ninety and three checks failed before it answered.

The second half is the missing bind mount. The image does not contain
`models/`, `app/main.py` opens `models/scaler.pkl` at import with no guard, and
the symptom is `HaltServer: Worker failed to boot` -- which names neither the
file nor the mount.
"""
import os
import re
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRYPOINT = os.path.join(REPO_ROOT, "entrypoint.sh")

#: What the load actually takes, from the production container's own log.
MEASURED_STARTUP_SECONDS = 15


def _healthcheck_field(name):
    with open(os.path.join(REPO_ROOT, "Dockerfile.prod"), encoding="utf-8") as fh:
        line = next(l for l in fh if l.startswith("HEALTHCHECK"))
    match = re.search(rf"--{name}=(\d+)s", line)
    assert match, f"{name} is not set on the HEALTHCHECK line"
    return int(match.group(1))


# --- the grace period ---------------------------------------------------------


def test_the_grace_period_covers_the_measured_startup():
    """With margin, because the measurement is one machine on one day.

    A period equal to the measured time is a coin toss on a slower host, which
    is exactly the host where #50 was observed.
    """
    period = _healthcheck_field("start-period")

    assert period >= MEASURED_STARTUP_SECONDS * 4, (
        f"start-period is {period}s against a measured {MEASURED_STARTUP_SECONDS}s "
        f"load; the stand where #50 was found took past 90s"
    )


def test_the_interval_and_retries_still_notice_a_real_failure():
    """A grace period long enough to hide a broken container would trade one
    wrong status for another."""
    assert _healthcheck_field("interval") <= 30
    assert int(re.search(r"--retries=(\d+)",
               open(os.path.join(REPO_ROOT, "Dockerfile.prod"),
                    encoding="utf-8").read()).group(1)) <= 3


# --- the missing mount --------------------------------------------------------


def _run_entrypoint(tmp_path, models_dir, args=()):
    """Run entrypoint.sh with the schema check stubbed out.

    `python3 ./scripts/verify_schema_head.py` needs a database. The models
    check sits before it in effect -- what is under test is whether the script
    refuses and what it says.
    """
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    (stub_dir / "python3").write_text("#!/bin/sh\nexit 0\n")
    (stub_dir / "gunicorn").write_text("#!/bin/sh\necho GUNICORN_STARTED\n")
    for name in ("python3", "gunicorn"):
        os.chmod(stub_dir / name, 0o755)

    env = dict(os.environ)
    env["PATH"] = f"{stub_dir}:{env['PATH']}"
    env["SORA_MODELS_DIR"] = str(models_dir)
    return subprocess.run(
        ["sh", ENTRYPOINT, *args],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=30,
    )


def test_a_missing_models_mount_is_refused_by_name(tmp_path):
    empty = tmp_path / "models"
    empty.mkdir()

    result = _run_entrypoint(tmp_path, empty)

    assert result.returncode == 1
    assert "scaler.pkl is missing" in result.stderr
    assert "./models:/app/models" in result.stderr, (
        "the refusal does not name the mount, which is the whole point"
    )
    assert "GUNICORN_STARTED" not in result.stdout


def test_a_present_mount_starts_the_server(tmp_path):
    """Otherwise the refusal is satisfied by a script that refuses always."""
    models = tmp_path / "models"
    models.mkdir()
    for name in ("scaler.pkl", "model.pkl"):
        (models / name).write_bytes(b"x")

    result = _run_entrypoint(tmp_path, models)

    assert result.returncode == 0, result.stderr
    assert "GUNICORN_STARTED" in result.stdout


def test_the_scheduler_override_is_checked_too(tmp_path):
    """compose mounts models/ into both services and run_scheduler.py reaches
    the same imports, so the override path must not skip the check."""
    empty = tmp_path / "models"
    empty.mkdir()

    result = _run_entrypoint(tmp_path, empty, args=("echo", "SCHEDULER"))

    assert result.returncode == 1
    assert "SCHEDULER" not in result.stdout


def test_the_entrypoint_still_does_not_migrate():
    """#125, restated here because this file edits the same script."""
    with open(ENTRYPOINT, encoding="utf-8") as fh:
        body = fh.read()

    assert not re.search(r"^[^#]*alembic\s+upgrade", body, re.M)
    assert "verify_schema_head" in body
