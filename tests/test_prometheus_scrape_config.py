"""The scrape config must name a service that exists and a path that serves.

Issue #94. A second `prometheus.yml` sat in the repository root, unmounted by
either compose file, pointing at `app:8000` — a host that exists only in
docker-compose.yml — and at `/metrics/prometheus`, which returns **404**. The
correct config, `infra/prometheus.yml`, is the one both compose files mount.

Nothing referenced the stale file, so nothing failed. Anyone who used it would
have got a target that never comes up, which Prometheus reports as a down
target rather than an error — the same silent shape as #91, where the
scheduler called a hostname that existed only in the dev compose file.

The file is deleted. What this test adds is the comparison that would have
caught it: the scrape target must be a service in the compose file that mounts
the config it appears in.
"""

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.prod.yml")


def _mounted_config(compose_file):
    """(config path, service names) for the prometheus config this file mounts."""
    doc = yaml.safe_load((REPO / compose_file).read_text())
    services = doc["services"]

    # Every name the network will resolve: service names plus any aliases.
    # An alias is how one shared scrape config can be correct in two files
    # that name the backend differently, so a test that only knew about
    # service names would reject the fix for the very problem it is testing.
    resolvable = set(services)
    for spec in services.values():
        networks = spec.get("networks") or {}
        if isinstance(networks, dict):
            for net in networks.values():
                if isinstance(net, dict):
                    resolvable.update(net.get("aliases") or [])

    mounts = services.get("prometheus", {}).get("volumes", []) or []
    for mount in mounts:
        source = str(mount).split(":")[0].lstrip("./")
        if source.endswith("prometheus.yml"):
            return REPO / source, resolvable

    pytest.fail(f"{compose_file} mounts no prometheus config")


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
def test_the_scrape_target_is_a_service_in_the_same_file(compose_file):
    """This is #94 and #91 in one shape: a host that exists somewhere else."""
    config, resolvable = _mounted_config(compose_file)
    assert config.is_file(), f"{compose_file} mounts {config}, which is missing"

    text = config.read_text()
    targets = re.findall(r"[\"']([A-Za-z0-9_.-]+):\d+[\"']", text)
    assert targets, f"{config} declares no scrape target"

    for host in targets:
        assert host in resolvable, (
            f"{config.name} scrapes {host!r}, which {compose_file} neither "
            f"defines as a service nor aliases. Resolvable there: "
            f"{sorted(resolvable)}"
        )


def test_no_unmounted_prometheus_config_is_left_lying_around():
    """A config nobody mounts is a trap, not a spare.

    The deleted one named a 404 path on a host absent from production. Its
    only possible use was by someone who assumed the file at the obvious
    location was the live one.
    """
    mounted = {_mounted_config(f)[0].resolve() for f in COMPOSE_FILES}
    # Scrape configs only. Grafana datasource provisioning files share the
    # name and are a different kind of file entirely -- an earlier version of
    # this test flagged two of them, which would have taught the next reader
    # to ignore it.
    on_disk = {
        p.resolve() for p in REPO.glob("**/prometheus.yml")
        if ".git" not in p.parts and "node_modules" not in p.parts
        and "scrape_configs" in p.read_text()
    }

    stray = on_disk - mounted
    assert not stray, (
        "prometheus config(s) that no compose file mounts: "
        + ", ".join(sorted(str(p.relative_to(REPO)) for p in stray))
    )
