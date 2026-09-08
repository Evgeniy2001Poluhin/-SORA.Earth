"""The production guards must be reachable, not merely present.

Three guards refuse to start a production deployment with development secrets:

    app/auth.py:28              a dev JWT signing key
    app/auth.py:371             the built-in account passwords
    app/secret_validation.py    every secret in REQUIRED_IN_PRODUCTION

Each is written correctly and covered by tests. On the running production host
all three were silent, because each is gated on `SORA_ENV == "production"` and
the application never received `SORA_ENV`. `docker-compose.prod.yml` passes
`ENV: production` and `JWT_SECRET`; the code reads `SORA_ENV` and
`SORA_JWT_SECRET`. Nothing in `app/` reads `ENV` or `JWT_SECRET`, so both
values arrived and were ignored, and the process fell back to the development
literals published in this repository. Reported privately as
GHSA-x724-6jh7-g3fr.

**Why the existing suite stayed green.** `tests/test_secret_validation.py`
calls `validate(env, "production")` and `enforce(env, "production")` — the
environment name is passed as an argument in every one of its cases. So it
proves the validator works *when told it is production* and never exercises the
step that tells it: `environ.get("SORA_ENV", "development")`. The fixture
supplied the condition under test, which is the one arrangement under which the
assertion cannot fail.

This file tests the wiring instead of the logic: that the deployment hands the
process the names it actually reads, and that the derivation from the
environment — not from an argument — reaches the guard.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
COMPOSE = REPO / "docker-compose.prod.yml"

#: The file developers actually run. It cannot have this file's *production*
#: defect -- there are no production guards to disarm and no secrets to lose --
#: but it can have the other half: a variable under a name nothing reads. That
#: half is what made the production one invisible, because a dead entry looks
#: exactly like a live one in both files.
DEV_COMPOSE = REPO / "docker-compose.yml"

#: Services that run the application in the development file. The names differ
#: from production on purpose: `app` there is `backend` here, and #278 is about
#: what happens when the two compose files are merged and both appear.
DEV_APP_SERVICES = ("app", "scheduler")

#: Services that run the application. `postgres`, `pgbouncer` and `grafana`
#: read their own configuration and are not in scope.
APP_SERVICES = ("backend", "scheduler")


def compose_environment(service: str, path: Path = COMPOSE) -> dict[str, str]:
    spec = yaml.safe_load(path.read_text())["services"][service]
    env = spec.get("environment", {})
    if isinstance(env, list):
        return dict(item.split("=", 1) for item in env if "=" in item)
    return {k: str(v) for k, v in env.items()}


def names_read_by_app() -> set[str]:
    """Environment names `app/` reads, by AST.

    `os.getenv("X")`, `os.environ["X"]` and `os.environ.get(X)` where `X` is a
    module-level string constant. That last form is why this resolves constants
    rather than matching literals: `app/paths.py` reads `SORA_RUNTIME_DIR`
    through `RUNTIME_DIR_ENV`, and a literals-only scan reported that live
    variable as dead — it would have had this test demand the deletion of a
    working entry.

    Names assembled at runtime — the `f"SORA_DEFAULT_{role.upper()}_PASSWORD"`
    in `app/auth.py` — still cannot be seen, which is why those are asserted by
    name below instead.
    """
    found: set[str] = set()
    for path in sorted((REPO / "app").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue

        constants = {
            target.id: node.value.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            for target in node.targets
            if isinstance(target, ast.Name)
        }

        def resolve(arg):
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return arg.value
            if isinstance(arg, ast.Name):
                return constants.get(arg.id)
            return None

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("getenv", "get")
                and node.args
            ):
                name = resolve(node.args[0])
                if name and name.isupper():
                    found.add(name)
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "environ"
            ):
                name = resolve(node.slice)
                if name:
                    found.add(name)
    return found


def test_the_scan_finds_what_it_judges():
    """Negative control. Every assertion below is over these two sets."""
    read = names_read_by_app()
    assert len(read) >= 20, f"only {len(read)} environment names parsed out of app/"
    assert "SORA_ENV" in read, "SORA_ENV is no longer read; the guards moved"

    for service in APP_SERVICES:
        assert compose_environment(service), f"{service} declares no environment"


@pytest.mark.parametrize("service", APP_SERVICES)
def test_the_application_is_told_it_is_production(service):
    """`SORA_ENV` must reach the process, under the name the process reads.

    Compose said `ENV: production` for months. It is the right intent under a
    name `app/` never consults, so all three guards stayed off while the file
    looked correctly configured.
    """
    env = compose_environment(service)

    assert "SORA_ENV" in env, (
        f"{service} is not given SORA_ENV, so app/auth.py and "
        f"app/secret_validation.py both take the development branch. "
        f"Present instead: {sorted(env)}"
    )
    assert env["SORA_ENV"] == "production", (
        f"{service} runs with SORA_ENV={env['SORA_ENV']!r}; the guards only "
        f"engage on exactly 'production'"
    )


@pytest.mark.parametrize("service", APP_SERVICES)
def test_compose_passes_no_name_the_application_never_reads(service):
    """A variable under the wrong name is worse than a missing one.

    It looks configured. `JWT_SECRET` is 64 characters of properly generated
    secret on the host and was discarded on arrival, while the process signed
    tokens with the literal in `app/auth.py`.

    Only names that look like application configuration are judged; the
    database, pooler and Grafana settings compose passes through belong to
    other images.
    """
    read = names_read_by_app()
    theirs = {
        "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB",
        "DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME",
        "POOL_MODE", "MAX_CLIENT_CONN", "DEFAULT_POOL_SIZE", "AUTH_TYPE",
        "WORKERS",
        # Read at app/auth.py:367 through an f-string, so the AST scan above
        # cannot see them by construction. Not an exemption from the rule --
        # `test_every_secret_the_code_requires_is_supplied` asserts each of
        # them is passed, which is the same claim from the other side.
        "SORA_DEFAULT_ADMIN_PASSWORD",
        "SORA_DEFAULT_ANALYST_PASSWORD",
        "SORA_DEFAULT_VIEWER_PASSWORD",
    }

    ignored = sorted(
        name
        for name in compose_environment(service)
        if name not in read and name not in theirs and not name.startswith("GF_")
    )

    assert not ignored, (
        f"{service} is given names nothing in app/ reads, so their values are "
        f"discarded on arrival: {ignored}. Either the code reads a different "
        f"name or the entry is dead."
    )


def test_every_secret_the_code_requires_is_supplied():
    """The names the guards demand, checked against what backend receives."""
    env = compose_environment("backend")
    missing = [
        name
        for name in (
            "SORA_JWT_SECRET",
            "SORA_ADMIN_TOKEN",
            "SORA_DEFAULT_ADMIN_PASSWORD",
            "SORA_DEFAULT_ANALYST_PASSWORD",
            "SORA_DEFAULT_VIEWER_PASSWORD",
        )
        if name not in env
    ]
    assert not missing, (
        "backend is not given these, so the application falls back to the "
        f"development literals in app/auth.py: {missing}"
    )


def test_the_guard_reads_the_environment_and_not_only_its_argument():
    """The path production takes, which the existing suite never exercises.

    `tests/test_secret_validation.py` passes `"production"` explicitly in every
    case. That proves the validator and skips the wiring. Here the name is
    derived from the mapping, exactly as `app/main.py` does at import.
    """
    from app.secret_validation import validate

    # Long enough to pass, not placeholders, and written as English so they do
    # not look like leaked secrets.
    #
    # `"x" * 40` is rejected by `_PLACEHOLDERS` (`xxx+`) -- correct of the
    # validator, and it failed the first version of this test for a reason
    # unrelated to what it checks. Hex strings passed the validator and tripped
    # gitleaks instead: three `generic-api-key` findings on this file, which is
    # the scanner being right. A fixture indistinguishable from a real leak
    # either forces an allowlist entry or teaches people to skip the scanner,
    # and `.gitleaks.toml` says so in its own first paragraph.
    #
    # `_PLACEHOLDERS` is anchored `^...$`, so a phrase containing "secret" is
    # fine while the bare word is not.
    unset = {
        "SORA_JWT_SECRET": "not-a-real-signing-key-only-this-test-uses-it",
        "SORA_ADMIN_TOKEN": "not-a-real-admin-token-for-tests",
        "POSTGRES_PASSWORD": "not-a-real-database-password",
    }

    assert validate(unset) == [], "without SORA_ENV nothing is policed — the state that shipped"

    armed = dict(unset, SORA_ENV="production")
    assert validate(armed) == [], "a complete production configuration must pass"

    weak = dict(armed, SORA_JWT_SECRET="tooshort")
    faults = validate(weak)
    assert faults, (
        "with SORA_ENV taken from the mapping, a short signing key must be "
        "reported; if this passes the derivation is broken and every guard is "
        "off in production"
    )
    assert not any("tooshort" in f for f in faults), "a fault must not echo the value"


#: Names belonging to other images, in both compose files. Kept beside the
#: assertions that use it rather than inside one of them: the two files pass
#: the same set, and a name exempted in one and not the other would be a
#: difference nobody chose.
NOT_OURS = frozenset({
    "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB",
    "DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME",
    "POOL_MODE", "MAX_CLIENT_CONN", "DEFAULT_POOL_SIZE", "AUTH_TYPE",
    "WORKERS",
    "SORA_DEFAULT_ADMIN_PASSWORD",
    "SORA_DEFAULT_ANALYST_PASSWORD",
    "SORA_DEFAULT_VIEWER_PASSWORD",
})


def dev_services_that_run_this_code() -> set[str]:
    """Services built from this repository, taken from the file.

    Derived rather than listed, because the list is exactly what a narrowing
    would quietly shrink: pointing `DEV_APP_SERVICES` at one service left
    every assertion green while the other stopped being judged. Measured.
    """
    cfg = yaml.safe_load(DEV_COMPOSE.read_text())["services"]
    return {name for name, spec in cfg.items() if spec.get("build")}


def test_the_development_compose_is_read_and_every_built_service_is_judged():
    """Negative control, in both directions the sweep can be wrong.

    Too narrow and a service escapes the rule; empty and the rule judges
    nothing. Both bounds come from the file, not from a number chosen here.
    """
    assert DEV_COMPOSE.exists(), f"{DEV_COMPOSE.name} is gone"

    built = dev_services_that_run_this_code()
    assert built, f"no service in {DEV_COMPOSE.name} is built from this repository"
    assert built <= set(DEV_APP_SERVICES), (
        f"these services run this code and are not judged: "
        f"{sorted(built - set(DEV_APP_SERVICES))}"
    )
    assert set(DEV_APP_SERVICES) <= built, (
        f"these are judged and do not run this code: "
        f"{sorted(set(DEV_APP_SERVICES) - built)}"
    )

    for service in DEV_APP_SERVICES:
        env = compose_environment(service, DEV_COMPOSE)
        # A name known to be live, not a count: a threshold defends nothing
        # once someone edits the threshold, and `RUN_SCHEDULER` is read by
        # `run_scheduler.py` and decides which of the two services this is.
        assert "RUN_SCHEDULER" in env, (
            f"{service} no longer declares RUN_SCHEDULER; either the file "
            f"changed shape or this check is reading the wrong service"
        )


@pytest.mark.parametrize("service", DEV_APP_SERVICES)
def test_the_development_compose_passes_no_dead_name(service):
    """The same rule as production, applied where people work every day.

    Not the same *defect*: development has no guards to disarm and no secrets
    to discard, so nothing here is a security matter. What is the same is the
    shape -- a variable under a name `app/` never consults looks configured and
    is not, and a developer who sets it and sees no effect has no way to tell
    which of the two is true.

    `SORA_ENV` is deliberately **not** required here. Development runs on
    defaults on purpose; demanding it would push people towards setting
    production values locally, which `app/secret_validation.py` says in its own
    docstring is the trade to avoid.

    Measured clean when this was written: seven services, ten variables between
    them, none dead. The check exists to keep that true, not to fix something.
    """
    read = names_read_by_app()
    ignored = sorted(
        name
        for name in compose_environment(service, DEV_COMPOSE)
        if name not in read and name not in NOT_OURS and not name.startswith("GF_")
    )

    assert not ignored, (
        f"{service} in {DEV_COMPOSE.name} is given names nothing in app/ reads, "
        f"so their values are discarded on arrival: {ignored}. In production "
        f"the same shape kept three guards switched off for four months."
    )
