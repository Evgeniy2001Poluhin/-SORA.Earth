"""The development stack's nginx must be able to start from this repository alone.

It could not, for two independent reasons, and the second was hidden behind the
first. Measured on 2026-09-21 against `docker-compose.yml` +
`docker-compose.override.yml`:

```
RestartCount=1536                       -- roughly 25.6 hours of looping
[emerg] cannot load certificate "/etc/letsencrypt/live/sora-earth.online/fullchain.pem"
getent hosts backend  -> nothing        -- inside the dev network
getent hosts app      -> 172.22.0.4
```

1. The development compose mounts `nginx/nginx.conf`, which is the production
   configuration: it terminates TLS with a Let's Encrypt certificate for
   `sora-earth.online`. A developer's machine has no such certificate, and
   `/etc/letsencrypt` mounts as an empty directory, so nginx exits before it
   serves anything and `restart: unless-stopped` retries it a minute later,
   forever.

2. That same configuration proxies to `backend:8000`. `backend` is the
   **production** service name; development calls the same process `app`.
   So fixing the certificate alone would have moved the failure from "exits at
   startup" to "502 on every request" -- a worse outcome, because a container
   that stays up looks healthy.

The second defect is why this file checks the upstreams rather than only the
certificate. A guard written against the error in the log would have passed a
stack that was still broken.

## What is asserted

Two properties, both about the *effective* nginx service -- the base file with
the override applied, which is what `docker compose up` runs:

- every host path it mounts is inside this repository. A path from the host's
  filesystem is by definition not guaranteed to exist;
- every upstream it proxies to names a service the development compose defines.

The merge is keyed by container target, which is what Compose does for
`volumes`; verified against `docker compose config` on 2026-09-21 rather than
read from the documentation.
"""
import os
import re

import pytest
import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(REPO, "docker-compose.yml")
OVERRIDE = os.path.join(REPO, "docker-compose.override.yml")

#: `server <host>:<port>` inside an `upstream` block, and `proxy_pass` to a
#: literal host. The named-upstream form is the one this configuration uses;
#: the direct form is matched too so that changing style does not silently
#: escape the check.
_UPSTREAM_SERVER = re.compile(r"^\s*server\s+([A-Za-z0-9_.-]+):(\d+)", re.M)
_PROXY_PASS_HOST = re.compile(r"proxy_pass\s+https?://([A-Za-z0-9_.-]+):(\d+)")

#: Names that are not compose services: loopback, and nginx's own resolver.
_NOT_A_SERVICE = {"127.0.0.11", "localhost", "127.0.0.1", "unix"}


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _volume_target(entry):
    """The container path of a compose volume entry, short or long form."""
    if isinstance(entry, dict):
        return entry.get("target")
    return entry.split(":")[1] if ":" in entry else entry


def _volume_source(entry):
    if isinstance(entry, dict):
        return entry.get("source")
    return entry.split(":")[0] if ":" in entry else None


def effective_nginx():
    """The nginx service as `docker compose up` sees it, base + override."""
    base = _load(BASE).get("services", {}).get("nginx", {}) or {}
    over = _load(OVERRIDE).get("services", {}).get("nginx", {}) or {}

    merged = dict(base)
    merged.update({k: v for k, v in over.items() if k != "volumes"})

    by_target = {_volume_target(v): v for v in base.get("volumes", [])}
    for v in over.get("volumes", []):
        by_target[_volume_target(v)] = v
    merged["volumes"] = list(by_target.values())
    return merged


def dev_services():
    return set((_load(BASE).get("services") or {}).keys())


def nginx_config_text():
    """The configuration file the effective service mounts at /etc/nginx/nginx.conf."""
    for entry in effective_nginx().get("volumes", []):
        if _volume_target(entry) == "/etc/nginx/nginx.conf":
            source = _volume_source(entry)
            path = os.path.normpath(os.path.join(REPO, source))
            with open(path, encoding="utf-8") as handle:
                return source, handle.read()
    raise AssertionError(
        "the development nginx service mounts nothing at /etc/nginx/nginx.conf, "
        "so this check cannot see which configuration it runs"
    )


def test_the_parsers_find_what_they_judge():
    """Without this, an empty parse would make every assertion below vacuous."""
    services = dev_services()
    assert "nginx" in services, "docker-compose.yml defines no nginx service"
    assert "app" in services, "docker-compose.yml defines no app service"

    source, text = nginx_config_text()
    assert text.strip(), f"{source} is empty"
    assert _UPSTREAM_SERVER.search(text) or _PROXY_PASS_HOST.search(text), (
        f"{source} names no upstream at all; the upstream check below would "
        "pass on any file"
    )


def test_nginx_mounts_nothing_from_outside_the_repository():
    """A host path is not guaranteed to exist, and an absent one is fatal here.

    `/etc/letsencrypt` mounted as an empty directory is what produced the
    1536-restart loop: nginx does not degrade when a certificate is missing, it
    refuses to start.
    """
    outside = []
    for entry in effective_nginx().get("volumes", []):
        source = _volume_source(entry)
        if source is None:
            continue
        if source.startswith("./") or source.startswith("../") or not os.path.isabs(source):
            continue
        outside.append((source, _volume_target(entry)))

    assert not outside, (
        "the development nginx mounts host paths that this repository does not "
        "provide, so the stack cannot start on a machine that only has the "
        "checkout:\n  "
        + "\n  ".join(f"{s} -> {t}" for s, t in outside)
        + "\nThe production compose is the place for host certificates; the "
        "development override should point nginx at a configuration that "
        "needs none."
    )


def test_every_upstream_names_a_service_the_dev_stack_defines():
    """The defect the certificate error hid.

    `backend:8000` is the production service name. In development the same
    process is `app`, and `backend` does not resolve -- measured with
    `getent hosts backend` inside the network, which returned nothing.
    """
    source, text = nginx_config_text()
    services = dev_services()

    unknown = []
    for pattern in (_UPSTREAM_SERVER, _PROXY_PASS_HOST):
        for host, port in pattern.findall(text):
            if host in _NOT_A_SERVICE or host in services:
                continue
            unknown.append(f"{host}:{port}")

    assert not unknown, (
        f"{source} proxies to hosts the development compose does not define: "
        + ", ".join(sorted(set(unknown)))
        + f"\nservices available: {', '.join(sorted(services))}"
        + "\nA name that does not resolve gives 502 on every request while the "
        "container stays up and looks healthy, which is harder to notice than "
        "a container that exits."
    )


@pytest.mark.parametrize("path", [BASE, OVERRIDE])
def test_the_compose_files_parse(path):
    """Both files must be readable YAML for anything above to mean anything."""
    assert _load(path).get("services"), f"{os.path.basename(path)} defines no services"
