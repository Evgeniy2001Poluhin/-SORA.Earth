"""backend and scheduler must share one runtime filesystem, or the lock is a lie.

Activation takes an `flock`. flock excludes two processes only when both open
the **same inode** — so two bind mounts of two host directories, or two
different `SORA_RUNTIME_DIR` values, would give each container its own lock file
and no mutual exclusion at all, while every in-process test still passed.

The subprocess test in `tests/test_model_source.py` proves the lock works
between processes in one environment. It cannot prove it works between
containers. This is the half a repository can check: that the declaration the
containers are built from puts them on one volume at one path.

The first version of this file matched the compose text with a regular
expression. That checks the spelling, not the configuration: an anchor, a
reordered key or a service name with a hyphen would all have broken it while the
deployment stayed correct — and, worse, a refactor that genuinely separated the
two volumes could have kept it passing. `yaml.safe_load` resolves anchors and
gives the structure the compose file actually declares.
"""
import os

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPOSE = os.path.join(REPO_ROOT, "docker-compose.prod.yml")

SERVICES = ("backend", "scheduler")
MOUNT_POINT = "/app/runtime"


@pytest.fixture(scope="module")
def compose():
    with open(COMPOSE, encoding="utf-8") as handle:
        parsed = yaml.safe_load(handle)
    assert parsed and "services" in parsed, "docker-compose.prod.yml declares no services"
    return parsed


def runtime_mounts(compose, service):
    """(source, target) for every mount landing on the runtime path.

    Handles both the short `name:/path` string form and the long mapping form,
    because either is valid compose and the test should not dictate which.
    """
    declared = compose["services"][service].get("volumes") or []
    found = []
    for entry in declared:
        if isinstance(entry, str):
            parts = entry.split(":")
            if len(parts) >= 2 and parts[1] == MOUNT_POINT:
                found.append((parts[0], parts[1]))
        elif isinstance(entry, dict) and entry.get("target") == MOUNT_POINT:
            found.append((entry.get("source"), entry.get("target")))
    return found


@pytest.mark.parametrize("service", SERVICES)
def test_each_service_mounts_something_at_the_runtime_path(compose, service):
    mounts = runtime_mounts(compose, service)
    assert mounts, (
        f"{service} mounts nothing at {MOUNT_POINT}; its activation lock would "
        "be a separate inode and exclude nobody"
    )


def test_both_services_mount_the_same_volume(compose):
    """A mismatch is the failure: each container locks its own file and two
    activations proceed at once, which is what the lock exists to prevent."""
    sources = {}
    for service in SERVICES:
        mounts = runtime_mounts(compose, service)
        assert len(mounts) == 1, f"{service} has {len(mounts)} runtime mounts"
        sources[service] = mounts[0][0]

    assert len(set(sources.values())) == 1, f"the volume sources differ: {sources}"


def test_the_shared_mount_is_a_named_volume(compose):
    """Two bind mounts of two host paths would look identical in each service
    and share nothing. A named volume is one filesystem by construction."""
    declared = compose.get("volumes") or {}
    source = runtime_mounts(compose, "backend")[0][0]

    assert source in declared, (
        f"{source!r} is not declared under top-level `volumes`, so it is a bind "
        "mount, and two bind mounts share no inode"
    )
    assert not source.startswith(("./", "/", "../")), (
        f"{source!r} is a host path, not a named volume"
    )


@pytest.mark.parametrize("service", SERVICES)
def test_each_service_points_at_the_mount_point(compose, service):
    environment = compose["services"][service].get("environment") or {}
    if isinstance(environment, list):
        environment = dict(
            item.split("=", 1) for item in environment if "=" in item)

    assert environment.get("SORA_RUNTIME_DIR") == MOUNT_POINT, (
        f"{service} does not set SORA_RUNTIME_DIR to {MOUNT_POINT}; the lock "
        "file would be somewhere the other container never opens"
    )


def test_both_services_agree_on_the_runtime_directory(compose):
    values = {}
    for service in SERVICES:
        environment = compose["services"][service].get("environment") or {}
        if isinstance(environment, list):
            environment = dict(
                item.split("=", 1) for item in environment if "=" in item)
        values[service] = environment.get("SORA_RUNTIME_DIR")

    assert len(set(values.values())) == 1, f"SORA_RUNTIME_DIR differs: {values}"
