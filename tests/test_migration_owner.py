"""One process migrates, and it is not an application container.

#125. `entrypoint.sh` ran `alembic upgrade head`, and the backend and the
scheduler share that file, so `docker compose up -d` started two migrators
against one database at the same instant. Verified against the running
production containers on 2026-08-13:

    backend    entrypoint=[/usr/bin/tini -- ./entrypoint.sh]  cmd=[]
    scheduler  entrypoint=[/usr/bin/tini -- ./entrypoint.sh]  cmd=[python3 -u run_scheduler.py]
    entrypoint.sh sha256 identical in both
    both DATABASE_URL -> pgbouncer:5432   pool_mode = transaction
    both restart=unless-stopped

Alembic takes no lock spanning that. The loser died, `restart: unless-stopped`
brought it back, and by then the migration had usually been applied. Five
deployments survived it, which is evidence about those five deployments.

The ordering and failure behaviour of the deployment step are covered by
tests/test_deploy_production.sh, which drives the script with a stubbed docker.
This file covers the parts that are statements about configuration and about the
verification the containers do instead.
"""
import os
import re

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPOSE = os.path.join(REPO_ROOT, "docker-compose.prod.yml")
ENTRYPOINT = os.path.join(REPO_ROOT, "entrypoint.sh")
DEPLOY = os.path.join(REPO_ROOT, "scripts", "deploy_production.sh")


@pytest.fixture(scope="module")
def compose():
    with open(COMPOSE) as fh:
        return yaml.safe_load(fh)


def _host(url):
    match = re.search(r"@([^/]+)/", url)
    return match.group(1) if match else None


# --- one owner --------------------------------------------------------------


def test_the_migrate_service_exists_and_runs_alembic(compose):
    service = compose["services"]["migrate"]

    assert service["entrypoint"] == ["alembic", "upgrade", "head"]
    assert service.get("restart") == "no", (
        "a migrator that restarts is the restart loop this replaces"
    )


def test_the_migrate_service_is_not_started_by_up(compose):
    """`profiles` is what keeps it from becoming a third migrator.

    Without it, `docker compose up -d` starts this service alongside backend and
    scheduler and the situation is strictly worse than before.
    """
    assert compose["services"]["migrate"].get("profiles"), (
        "migrate has no profile, so `docker compose up` will start it"
    )


def test_the_migrator_bypasses_the_connection_pooler(compose):
    """A pooler in transaction mode does not preserve session state.

    Nothing has needed it yet. A future migration using an advisory lock, a
    temp table, or `SET LOCAL` outside its own transaction would find out on
    production -- so the migrator is pointed at PostgreSQL directly, and this
    records that the two targets are genuinely different rather than the same
    string written twice.
    """
    services = compose["services"]

    migrate_host = _host(services["migrate"]["environment"]["DATABASE_URL"])
    backend_host = _host(services["backend"]["environment"]["DATABASE_URL"])

    assert migrate_host, services["migrate"]["environment"]["DATABASE_URL"]
    assert backend_host, services["backend"]["environment"]["DATABASE_URL"]
    assert migrate_host != backend_host, (
        f"the migrator and the application share a target ({migrate_host}); "
        f"the whole point is that one of them is the pooler and one is not"
    )
    assert migrate_host.startswith("postgres:"), migrate_host
    assert backend_host.startswith("pgbouncer:"), backend_host


def test_the_application_services_still_use_the_pooler(compose):
    """The change must not quietly move application traffic off the pooler.

    That would be a capacity change smuggled in with a correctness fix.
    """
    for name in ("backend", "scheduler"):
        host = _host(compose["services"][name]["environment"]["DATABASE_URL"])
        assert host.startswith("pgbouncer:"), (name, host)


# --- the application containers do not migrate ------------------------------


def test_the_entrypoint_runs_no_ddl():
    """Read rather than executed: the property is what the file contains.

    Comment lines are excluded -- the file explains at length why the alembic
    call is gone, and a naive grep would match the explanation.
    """
    lines = [
        line for line in open(ENTRYPOINT).read().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    body = "\n".join(lines)

    assert "alembic" not in body, (
        "entrypoint.sh still invokes alembic; both application containers share "
        "this file and would migrate concurrently again"
    )
    assert "verify_schema_head" in body, (
        "the entrypoint no longer migrates and no longer verifies either, so a "
        "container will start against a database the migrations never reached"
    )


def test_both_application_services_share_this_entrypoint(compose):
    """The reason one file matters. Asserted so the premise stays true.

    If the two ever stop sharing it, the test above covers one container and
    silently stops covering the other.
    """
    services = compose["services"]
    for name in ("backend", "scheduler"):
        assert "entrypoint" not in services[name], (
            f"{name} overrides the entrypoint; this file's reasoning about a "
            f"single shared entrypoint no longer holds"
        )


def test_the_deploy_script_migrates_before_it_recreates():
    """Order in the file, as a cheap guard on top of the behavioural test.

    tests/test_deploy_production.sh asserts the order of the recorded docker
    calls, which is the real check. This one fails faster and without a stubbed
    daemon, and it catches the specific edit -- moving the step below `up` --
    that would otherwise need the whole harness to notice.
    """
    source = open(DEPLOY).read()

    # Anchored inside the mutating phase, not on the first occurrence in the
    # file. `abort_deployment` is defined earlier and contains an
    # `up -d --build --remove-orphans` of its own, so a naive `find` compared
    # the migration against the rollback's call and failed for a reason that
    # had nothing to do with the order it names.
    phase_at = source.find("journal_write mutating")
    migrate_at = source.find("run --rm --build migrate", phase_at)
    recreate_at = source.find("up -d --build --remove-orphans", phase_at)

    assert phase_at != -1, "the mutating phase is no longer marked"
    assert migrate_at != -1, "the deploy script no longer runs the migrator"
    assert recreate_at != -1, "the deploy script no longer recreates the containers"
    assert migrate_at < recreate_at, (
        "the migration step is below the container recreate, so the new code "
        "starts against the old schema"
    )
    # After the journal is written, not before: a migration is a mutation, and
    # a run that dies between it and the recreate has to leave a record saying
    # so.
    assert phase_at < migrate_at


# --- the verification itself ------------------------------------------------


def test_a_missing_database_url_is_refused_not_assumed():
    from scripts.verify_schema_head import check

    ok, message = check(url="")

    assert ok is False
    assert "DATABASE_URL" in message


def test_an_unreachable_database_is_refused():
    """Not "assume it is fine".

    A verification that passes when it cannot see anything is the shape of
    every defect this repository has been closing.
    """
    from scripts.verify_schema_head import check

    ok, message = check(url="postgresql://nobody@127.0.0.1:1/nothing")

    assert ok is False
    assert "could not read the schema version" in message


# --- against a real database ------------------------------------------------
#
# The four states are only distinguishable on a database that actually records
# a revision. On SQLite the migrations do not run at all, so these skip -- see
# tests/postgres_scratch.py for why that engine is the only honest one here.


def _scratch(url, name):
    """A fresh database, dropped afterwards."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    base = make_url(url)
    admin = create_engine(base.set(database="postgres"),
                          isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text('DROP DATABASE IF EXISTS "%s"' % name))
        conn.execute(text('CREATE DATABASE "%s"' % name))
    return base.set(database=name), admin


def _drop(admin, name):
    from sqlalchemy import text

    with admin.connect() as conn:
        conn.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :n AND pid <> pg_backend_pid()"), {"n": name})
        conn.execute(text('DROP DATABASE IF EXISTS "%s"' % name))
    admin.dispose()


@pytest.fixture
def fresh_database():
    import uuid

    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("needs a PostgreSQL DATABASE_URL; alembic_version is the subject")

    name = "sora_headcheck_%s" % uuid.uuid4().hex[:12]
    scratch, admin = _scratch(url, name)
    try:
        yield scratch.render_as_string(hide_password=False)
    finally:
        _drop(admin, name)


def _alembic(url, *args):
    import subprocess
    import sys

    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT, env={**os.environ, "DATABASE_URL": url},
        capture_output=True, text=True, timeout=300,
    )


def test_a_database_that_was_never_migrated_is_refused(fresh_database):
    from scripts.verify_schema_head import check

    ok, message = check(url=fresh_database)

    assert ok is False
    assert "never been migrated" in message
    # The instruction has to be the one that works, not "run alembic".
    assert "run --rm migrate" in message


def test_a_database_behind_head_names_what_is_missing(fresh_database):
    """The case the whole change exists for.

    A container started against this would work until the first query touching
    a column the missing revision adds -- at request time, to a user.
    """
    from alembic.script import ScriptDirectory

    from scripts.verify_schema_head import check, _config

    script = ScriptDirectory.from_config(
        _config(os.path.join(REPO_ROOT, "alembic"), fresh_database))
    head = script.get_current_head()
    behind = script.get_revision(head).down_revision
    if behind is None:
        pytest.skip("only one revision in the history; nothing to be behind")

    assert _alembic(fresh_database, "upgrade", behind).returncode == 0

    ok, message = check(url=fresh_database)

    assert ok is False
    assert "behind by 1 revision" in message
    assert head in message, "the message does not name the revision to run"


def test_a_database_at_head_is_accepted(fresh_database):
    """Otherwise the refusals above could be satisfied by refusing everything."""
    from scripts.verify_schema_head import check

    result = _alembic(fresh_database, "upgrade", "head")
    assert result.returncode == 0, result.stderr[-2000:]

    ok, message = check(url=fresh_database)

    assert ok is True, message
    assert "at head" in message


def test_a_revision_from_another_history_is_named_as_such(fresh_database):
    """Different from "behind", and it needs a different response.

    A recorded revision this checkout has never heard of means the image and
    the database are on different histories -- deploying the wrong build, not
    forgetting a migration. Telling someone to run the migrator would be wrong
    advice.
    """
    from sqlalchemy import create_engine, text

    from scripts.verify_schema_head import check

    assert _alembic(fresh_database, "upgrade", "head").returncode == 0

    engine = create_engine(fresh_database)
    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = 'deadbeef1234'"))
    engine.dispose()

    ok, message = check(url=fresh_database)

    assert ok is False
    assert "not a head of this checkout" in message
    assert "run --rm migrate" not in message, (
        "this is not a missing migration and must not be described as one"
    )
