"""The unit files that turn tested scripts into a backup that happens.

GAP-007 was marked PARTIAL for a precise reason: the scripts were merged and
covered by tests, and nothing scheduled them. These assertions are about the
seams between the unit and the code it runs -- the places where a plausible unit
file quietly does the wrong thing.
"""
import configparser
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
UNITS = REPO / "infra" / "systemd"


def _unit(name):
    """systemd files are ini-like, but keys repeat, so let the parser keep them."""
    parser = configparser.RawConfigParser(strict=False, allow_no_value=True)
    parser.optionxform = str
    parser.read(UNITS / name)
    return parser


@pytest.fixture(scope="module")
def service():
    return _unit("sora-backup.service")


@pytest.fixture(scope="module")
def timer():
    return _unit("sora-backup.timer")


def test_a_skipped_run_is_not_recorded_as_a_failure(service):
    """backup_run.sh exits 75 when another run holds the lock -- EX_TEMPFAIL,
    deliberately not an error. Without SuccessExitStatus systemd calls that a
    failed unit, and a benign overlap pages somebody: exactly the outcome the
    LOCK_SKIP flag prevents one layer down."""
    assert service.get("Service", "SuccessExitStatus") == "75"


def test_the_runtime_directory_is_the_one_the_lock_demands(service):
    """scripts/backup_lock.sh refuses to run in production without a 0700
    directory it owns, and its error message names these two settings."""
    assert service.get("Service", "RuntimeDirectory") == "sora-earth"
    assert service.get("Service", "RuntimeDirectoryMode") == "0700"
    assert service.get("Service", "Environment") == "BACKUP_RUNTIME_DIR=/run/sora-earth"


def test_the_lock_would_accept_that_directory():
    """The mode has to satisfy validate_runtime_dir's own arithmetic, not merely
    look restrictive: it rejects anything with a group or other bit set."""
    service_text = (UNITS / "sora-backup.service").read_text()
    mode = [l for l in service_text.splitlines() if l.startswith("RuntimeDirectoryMode=")][0]
    octal = int(mode.split("=", 1)[1], 8)
    assert octal & 0o077 == 0, f"{oct(octal)} would be refused by validate_runtime_dir"


def test_the_service_runs_the_script_that_exists(service):
    exec_start = service.get("Service", "ExecStart")
    script = exec_start.split()[0]
    assert script.endswith("/scripts/backup_run.sh")
    assert (REPO / "scripts" / "backup_run.sh").exists()
    assert exec_start.split()[1:], "no database argument; backup_run.sh exits 2 without one"


def test_a_missed_backup_is_not_simply_lost(timer):
    """A host down at 03:00 would otherwise have no backup for that night, and
    nothing would say so."""
    assert timer.get("Timer", "Persistent") == "true"


def test_the_schedule_is_spread(timer):
    assert timer.get("Timer", "OnCalendar")
    assert timer.get("Timer", "RandomizedDelaySec")


def test_both_units_are_installable(service, timer):
    assert service.get("Install", "WantedBy") == "multi-user.target"
    assert timer.get("Install", "WantedBy") == "timers.target"


def test_no_secret_is_written_into_the_unit(service):
    """The envelope's premise is that this host holds only a public key."""
    text = (UNITS / "sora-backup.service").read_text()
    assert "EnvironmentFile=" in text
    for marker in ("SECRET", "PASSWORD", "AWS_SECRET", "PRIVATE KEY", "IDENTITY_KEY"):
        assert marker not in text.upper().replace("EnvironmentFile=".upper(), ""), marker
