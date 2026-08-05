"""The host the scheduler calls must exist in the compose file it runs under.

Issue #91. `refresh_forecast_metrics` called `http://app:8000`. That is the
backend's service name in docker-compose.yml and not in
docker-compose.prod.yml, where it is `backend` -- so in production the name did
not resolve and the job raised on every run, twice a minute, from 2026-07-17
until it was found by reading the logs after an unrelated deployment.

The commit that introduced it was titled "change backend to app service name in
nginx and scheduler": it fixed development and broke production in one edit,
and nothing compared the name against the file it would run under. That
comparison is what these tests are.
"""

import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
import yaml

from app.scheduler import backend_base_url

REPO = Path(__file__).resolve().parent.parent
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.prod.yml")


def _services_and_url(compose_file):
    """(service names, the scheduler's SORA_BACKEND_URL) from one file."""
    doc = yaml.safe_load((REPO / compose_file).read_text())
    services = doc["services"]

    env = services["scheduler"].get("environment", {})
    # Compose accepts either a mapping or a list of KEY=VALUE strings, and the
    # two files here use one each.
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env if "=" in item)

    return set(services), env.get("SORA_BACKEND_URL")


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
def test_backend_url_is_set_explicitly(compose_file):
    """Neither file may rely on the default: it can only be right in one."""
    _, url = _services_and_url(compose_file)
    assert url, f"{compose_file} leaves SORA_BACKEND_URL to the default"


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
def test_backend_url_names_a_service_in_the_same_file(compose_file):
    """This is #91 itself, and it is checked per file rather than globally.

    A host that exists in *some* compose file is exactly the state that
    shipped: `app` was real, just not here.
    """
    services, url = _services_and_url(compose_file)
    host = urlparse(url).hostname

    assert host in services, (
        f"{compose_file}: the scheduler calls '{host}', which is not a service "
        f"in this file. Defined here: {sorted(services)}"
    )


def test_the_default_matches_production(monkeypatch):
    """Unset, it must name production's service, not development's.

    A default is what runs when someone adds a compose file, or runs the
    scheduler outside compose. Pointing it at the environment where a silent
    failure is cheap gets that backwards.
    """
    monkeypatch.delenv("SORA_BACKEND_URL", raising=False)
    prod_services, _ = _services_and_url("docker-compose.prod.yml")

    assert urlparse(backend_base_url()).hostname in prod_services


def test_the_url_is_read_at_call_time(monkeypatch):
    """Read per call, not captured at import, or a test cannot set it."""
    monkeypatch.setenv("SORA_BACKEND_URL", "http://somewhere-else:9999")
    assert backend_base_url() == "http://somewhere-else:9999"


def test_a_trailing_slash_does_not_double(monkeypatch):
    """The caller appends '/api/v1/...', so a trailing slash would give '//'."""
    monkeypatch.setenv("SORA_BACKEND_URL", "http://backend:8000/")
    assert backend_base_url() == "http://backend:8000"
