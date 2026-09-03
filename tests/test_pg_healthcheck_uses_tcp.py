"""A readiness check must speak the transport its waiters use.

`migrate` starts on `depends_on: postgres: condition: service_healthy`, and it
reaches the database over TCP. The healthcheck deciding that condition asked
over the Unix socket, so the two were measuring different things and the gap had
a window in it.

`initdb` is that window. The postgres entrypoint runs a temporary server bound to
the socket alone, logs "database system is ready to accept connections" with
nothing listening on 5432, then stops it and starts the real one. A socket-based
`pg_isready` passes there. Measured on the first deployment to a fresh server,
2026-09-02:

    20:21:38.905  temporary server: listening on Unix socket
    20:21:38.917  "ready to accept connections"        <- no TCP listener
    20:21:39.070  temporary server shutting down
    20:21:39.192  real server: listening on IPv4 0.0.0.0:5432

`alembic upgrade head` got ECONNREFUSED and the deployment refused at exit 76.

The bug needs an empty volume, so it cannot appear on a server that already has
a database. It appears when one is built from nothing — which is the restore
path, during an incident.

These assertions are over the parsed compose file rather than its text: an
anchor, a reordered key or a renamed service would all break a regex while the
deployment stayed correct, and — worse — a change that genuinely reintroduced a
socket-only check could keep one passing.
"""
import os
import shlex

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPOSE = os.path.join(REPO_ROOT, "docker-compose.prod.yml")

#: `-h` with any of these means the check is going over TCP. Not a literal
#: match on one spelling: the property is the transport, not the address.
LOOPBACK = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


@pytest.fixture(scope="module")
def compose():
    with open(COMPOSE, encoding="utf-8") as handle:
        parsed = yaml.safe_load(handle)
    assert parsed and "services" in parsed, "docker-compose.prod.yml declares no services"
    return parsed


def healthcheck_command(service):
    """The check as a token list, or None when the service declares none."""
    test = (service.get("healthcheck") or {}).get("test")
    if not test:
        return None
    if isinstance(test, str):
        return shlex.split(test)
    # ["CMD-SHELL", "..."] and ["CMD", "prog", "arg"] are both valid compose.
    tokens = []
    for part in test:
        if part in ("CMD", "CMD-SHELL", "NONE"):
            continue
        tokens.extend(shlex.split(part))
    return tokens


def pg_isready_services(compose):
    """Every service whose healthcheck runs pg_isready — not just `postgres`.

    Enumerated rather than named, so a second database service added later is
    held to the same rule instead of being exempt by omission.
    """
    found = {}
    for name, service in (compose.get("services") or {}).items():
        tokens = healthcheck_command(service or {})
        if tokens and any(t.endswith("pg_isready") for t in tokens):
            found[name] = tokens
    return found


def test_there_is_a_pg_isready_healthcheck_to_talk_about(compose):
    """Guards the two tests below: if the check were renamed or dropped they
    would pass over an empty set and report nothing."""
    assert pg_isready_services(compose), (
        "no service declares a pg_isready healthcheck; the checks below would "
        "have nothing to assert against and would pass vacuously"
    )


def test_every_pg_isready_check_goes_over_tcp(compose):
    for name, tokens in pg_isready_services(compose).items():
        assert "-h" in tokens or "--host" in tokens, (
            f"{name}'s healthcheck runs pg_isready with no host, so it asks over "
            f"the Unix socket. During initdb a socket-only server answers it "
            f"while nothing listens on TCP, and whatever waits on "
            f"`service_healthy` then fails to connect: {' '.join(tokens)}"
        )
        flag = "-h" if "-h" in tokens else "--host"
        host = tokens[tokens.index(flag) + 1]
        assert host in LOOPBACK, (
            f"{name}'s healthcheck names host {host!r}; it must test the "
            f"container's own TCP listener, not another host"
        )


def test_whatever_waits_on_postgres_health_uses_tcp_itself(compose):
    """The rule exists because of the waiters. If nothing waited on
    `service_healthy`, a socket check would be harmless."""
    waiters = []
    for name, service in (compose.get("services") or {}).items():
        depends = (service or {}).get("depends_on") or {}
        if not isinstance(depends, dict):
            continue
        for target, spec in depends.items():
            if target in pg_isready_services(compose) and isinstance(spec, dict) \
                    and spec.get("condition") == "service_healthy":
                waiters.append(name)

    assert waiters, (
        "nothing waits on a postgres healthcheck; either the dependency was "
        "removed — in which case this file's premise is gone — or it is spelled "
        "in a way this test no longer recognises"
    )
    # Documented, not asserted about their internals: these connect by DNS name
    # over TCP, which is exactly why the check may not use the socket.
    assert "migrate" in waiters or "backend" in waiters, (
        f"expected the migration or backend service among the waiters, got {waiters}"
    )
