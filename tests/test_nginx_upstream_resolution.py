"""nginx must not freeze an upstream address when its workers start.

On 2026-08-09 a deploy recreated the backend, it took the address the scheduler
had been using, and nginx kept sending to the old one: 4.5 minutes of 502 on
the public endpoint while `docker inspect` reported both containers healthy
with zero restarts (#129). Name resolution was never broken -- `getent hosts
backend` inside the nginx container returned the new address throughout. Only
the cached one was stale.

There are two independent defences, and these tests cover both because they
protect different events:

  1. `scripts/deploy_production.sh` recreates nginx after the backend and
     verifies the public endpoint. It already existed; the incident happened
     because it was bypassed by a manual `docker compose up`.
  2. `resolver` plus `resolve` on the upstream, so an out-of-band recreate --
     one that never goes through the script -- recovers on its own.

The behavioural test for the second layer needs live containers and lives in
the production acceptance for #129, not here: a unit test cannot move a
container's IP. What is asserted here is that the configuration says what it
must, and that the script's guarantees have not quietly gone away.
"""
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONF = (ROOT / "nginx" / "nginx.conf").read_text()
DEPLOY = (ROOT / "scripts" / "deploy_production.sh").read_text()


# --- layer 2: the configuration re-resolves -------------------------------


def test_a_resolver_is_configured():
    """Without one, `resolve` on an upstream is a configuration error."""
    assert re.search(r"^\s*resolver\s+127\.0\.0\.11\b", CONF, re.M), (
        "Docker's embedded DNS is not configured as the resolver"
    )


def test_the_resolver_result_expires():
    """A cache that never expires is the failure with extra steps."""
    m = re.search(r"^\s*resolver\s+127\.0\.0\.11\s+valid=(\d+)s", CONF, re.M)
    assert m, "the resolver has no `valid=` window"
    assert 1 <= int(m.group(1)) <= 60, (
        f"valid={m.group(1)}s is outside a useful range: long enough to be a "
        f"stale-address window, or short enough to be a lookup per request"
    )


@pytest.mark.parametrize("name,host", [
    ("sora_backend", "backend:8000"),
    ("sora_grafana", "grafana:3000"),
])
def test_each_upstream_re_resolves(name, host):
    """`resolve` needs a shared-memory zone; without it nginx refuses to start,
    so the two are asserted together rather than separately."""
    block = re.search(
        rf"upstream\s+{name}\s*\{{(.*?)\}}", CONF, re.S
    )
    assert block, f"upstream {name} is missing"
    body = block.group(1)

    assert re.search(rf"^\s*server\s+{re.escape(host)}\s+resolve\s*;", body, re.M), (
        f"{name} resolves {host} once at startup; a container recreate strands it"
    )
    assert re.search(rf"^\s*zone\s+{name}\s+\S+;", body, re.M), (
        f"{name} has `resolve` without a zone, which nginx rejects"
    )


def test_no_proxy_pass_targets_a_bare_hostname():
    """A literal host in proxy_pass is resolved at config load, like the old
    upstream was. Named upstreams keep the URI semantics that a variable target
    would change."""
    bare = [
        line.strip()
        for line in CONF.splitlines()
        if "proxy_pass" in line
        and re.search(r"proxy_pass\s+https?://(?!sora_)[a-z][\w.-]*[:/]", line)
    ]
    assert bare == [], f"proxy_pass targets a hostname directly: {bare}"


# --- layer 1: the supported deploy path still guarantees what it claims ----


def test_the_deploy_script_recreates_nginx_after_the_backend():
    """The ordering is the guarantee. nginx must re-resolve *after* the new
    backend exists, or it caches the address that is about to be replaced."""
    up = DEPLOY.index('up -d --build --remove-orphans', DEPLOY.index('step "deploying"'))
    recreate = DEPLOY.index("up -d --force-recreate nginx", up)

    assert recreate > up, (
        "nginx is recreated before the backend, so it can cache the old address"
    )


def test_the_deploy_script_validates_the_nginx_configuration():
    assert "nginx -t" in DEPLOY, (
        "the deploy no longer checks that nginx accepts its own configuration"
    )


def test_the_deploy_script_checks_the_public_endpoint():
    """Container health is measured inside the container and cannot see the
    path a user takes. During the incident it was green throughout."""
    assert "sora-earth.online" in DEPLOY, (
        "the deploy verifies nothing from outside; a 502 would pass unnoticed"
    )
    assert re.search(r"https://[^\s\"']*sora-earth\.online", DEPLOY), (
        "the external check does not use https, which is the scheme users get"
    )


def test_the_deploy_script_inspects_the_upstream_it_deployed():
    assert "upstream sora_backend" in DEPLOY, (
        "the deploy no longer reads back the upstream it is serving through"
    )


# --- documentation ---------------------------------------------------------


def test_the_project_instructions_name_the_supported_path():
    """CLAUDE.md carried the manual command that caused the incident, and it is
    loaded into every session -- so the wrong instruction propagates."""
    claude = (ROOT / "CLAUDE.md").read_text()

    assert "scripts/deploy_production.sh" in claude, (
        "the project instructions do not name the supported deployment path"
    )

    # Anchored on the production section specifically: CLAUDE.md has an
    # earlier "**Deployment:**" line describing the compose topology, and
    # slicing from the first match tested the wrong paragraph.
    marker = "**Deployment — one supported way"
    assert marker in claude, "the production deployment section was renamed"
    # Whitespace-normalised: the paragraph is hard-wrapped, so "not a
    # deployment procedure" spans a newline in the file. A substring test that
    # depends on where a line happens to break tests the formatting.
    deployment_section = " ".join(claude[claude.index(marker):][:1800].split())
    assert "not a deployment procedure" in deployment_section, (
        "the instructions do not say that a manual compose up is not a deploy"
    )
    assert not re.search(
        r"^git pull && docker compose .*up -d --build", deployment_section, re.M
    ), "the manual deploy command is still presented as the way to deploy"
