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
    modes = [
        line.split("=", 1)[1].strip()
        for line in service_text.splitlines()
        if line.startswith("RuntimeDirectoryMode=")
    ]
    assert modes, "the unit sets no RuntimeDirectoryMode"

    octal = int(modes[0], 8)
    # No group or other bits, which is what validate_runtime_dir checks --
    assert octal & 0o077 == 0, f"{oct(octal)} would be refused by validate_runtime_dir"
    # -- and the owner must actually be able to use it. The bitmask alone
    # accepts 0500, which passes validate_runtime_dir and then leaves the
    # service unable to create its own lock file: refused for a reason the
    # test was written to rule out.
    assert octal == 0o700, f"{oct(octal)} is not 0700; the owner needs write and search"


def test_the_service_runs_the_script_that_exists(service):
    exec_start = service.get("Service", "ExecStart")
    script = exec_start.split()[0]
    assert script.endswith("/scripts/backup_run.sh")
    assert (REPO / "scripts" / "backup_run.sh").exists()
    # The exact command, not merely "some argument".
    #
    # `split()[1:]` is true for any non-empty tail, so backing up the wrong
    # database would pass -- and a backup of the wrong database is worse than no
    # backup, because it looks like one.
    assert exec_start.split()[1:] == ["sora_earth"], exec_start


def test_a_missed_backup_is_not_simply_lost(timer):
    """A host down at 03:00 would otherwise have no backup for that night, and
    nothing would say so."""
    assert timer.get("Timer", "Persistent") == "true"


def test_the_schedule_is_spread(timer):
    """The documented schedule, not merely the presence of the keys.

    These asserted truthiness, so `OnCalendar` at any hour passed and
    `RandomizedDelaySec=0` -- no spreading at all -- passed too. Neither
    protected what the document promises.
    """
    assert timer.get("Timer", "OnCalendar") == "*-*-* 03:00:00", (
        "the documented nightly 03:00 schedule"
    )
    delay = timer.get("Timer", "RandomizedDelaySec")
    assert delay not in ("0", "0s", None), (
        "RandomizedDelaySec=0 spreads nothing; hosts would all start together"
    )


def test_both_units_are_installable(service, timer):
    assert service.get("Install", "WantedBy") == "multi-user.target"
    assert timer.get("Install", "WantedBy") == "timers.target"


def test_no_secret_is_written_into_the_unit(service):
    """The envelope's premise is that this host holds only a public key."""
    text = (UNITS / "sora-backup.service").read_text()

    # The policy, not a list of words that might appear in a secret.
    #
    # A denylist passes anything nobody thought of: `Environment=BACKUP_S3_ACCESS_KEY_ID=...`
    # contains none of SECRET, PASSWORD, AWS_SECRET, PRIVATE KEY or IDENTITY_KEY,
    # and would have sailed through while putting a credential in a
    # world-readable unit file.
    #
    # The rule is simpler and complete: secrets arrive through EnvironmentFile,
    # and the only inline Environment= permitted is the runtime directory, which
    # is a path rather than a credential.
    assert "EnvironmentFile=/etc/sora-earth/backup.env" in text

    inline = [
        line.strip() for line in text.splitlines()
        if line.strip().startswith("Environment=")
    ]
    assert inline == ["Environment=BACKUP_RUNTIME_DIR=/run/sora-earth"], (
        "an inline Environment= assignment other than the runtime directory: %r" % inline
    )
