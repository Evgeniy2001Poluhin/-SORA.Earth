"""backend and scheduler must share one runtime filesystem, or the lock is a lie.

Activation takes an `flock`. flock excludes two processes only when both open
the **same inode** — so two bind mounts of two host directories, or two
different `SORA_RUNTIME_DIR` values, would give each container its own lock file
and no mutual exclusion at all, while every in-process test still passed.

The subprocess test in `tests/test_model_source.py` proves the lock works
between processes in one environment. It cannot prove it works between
containers. This is the half a repository can check: that the declaration the
containers are built from actually puts them on one volume at one path.
"""
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPOSE = os.path.join(REPO_ROOT, "docker-compose.prod.yml")

SERVICES = ("backend", "scheduler")


def service_block(name):
    with open(COMPOSE, encoding="utf-8") as handle:
        body = handle.read()
    match = re.search(rf"^  {name}:\n(.*?)(?=^  [a-z_]+:\n|\Z)", body,
                      re.MULTILINE | re.DOTALL)
    assert match, f"{name} is not declared in docker-compose.prod.yml"
    return match.group(1)


@pytest.mark.parametrize("service", SERVICES)
def test_each_service_mounts_the_runtime_volume(service):
    block = service_block(service)
    assert "runtime_models:/app/runtime" in block, (
        f"{service} does not mount the shared runtime volume; its activation "
        "lock would be a separate inode and exclude nobody"
    )


@pytest.mark.parametrize("service", SERVICES)
def test_each_service_points_at_the_same_runtime_dir(service):
    block = service_block(service)
    assert "SORA_RUNTIME_DIR: /app/runtime" in block, (
        f"{service} does not set SORA_RUNTIME_DIR to the mount point"
    )


def test_both_services_agree_on_the_mount_point():
    """A mismatch is the failure mode: each container locks its own file and
    two activations proceed at once."""
    mounts = {}
    for service in SERVICES:
        block = service_block(service)
        found = re.findall(r"runtime_models:(\S+)", block)
        assert found, f"{service} has no runtime mount"
        mounts[service] = found[0]
    assert len(set(mounts.values())) == 1, f"the mount points differ: {mounts}"


def test_the_volume_is_named_not_a_bind_mount():
    """Two bind mounts of two host paths would look identical in each service
    and share nothing."""
    with open(COMPOSE, encoding="utf-8") as handle:
        body = handle.read()
    assert re.search(r"^volumes:\n(?:.*\n)*?  runtime_models:", body, re.MULTILINE), (
        "runtime_models is not declared as a named volume"
    )
    for service in SERVICES:
        assert "./runtime:/app/runtime" not in service_block(service), (
            f"{service} bind-mounts a host directory instead of the shared volume"
        )
