"""Refuse to start on a database the migrations have not reached.

#125. `entrypoint.sh` ran `alembic upgrade head` in both the backend and the
scheduler, so `docker compose up -d` started two migrators against one database
at the same moment. That is now a single deployment step
(`docker compose run --rm migrate`), and the application containers verify
instead of migrate.

Read-only, deliberately: it opens a connection, reads `alembic_version`, and
compares it with the heads on disk. It takes no lock, writes nothing, and runs
no DDL, so it is safe in as many replicas as there are.

Exit codes are the interface:

    0   the database is at head
    1   the database is behind, ahead, or unreachable

Behind is the interesting case and the message says which revisions are
missing, because "run alembic upgrade head" is useless advice to whoever is
reading a crash-looped container at 3am.

This is not a substitute for `assert_schema_ready()` in app/main.py, which
compares tables and columns against the models. A migration that failed halfway
leaves `alembic_version` untouched and the schema wrong, and only the column
check catches that. This catches the opposite: a deploy that skipped migrations
entirely, where the recorded revision is honest and simply old.
"""
import os
import sys


def _config(script_location, url):
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", script_location)
    config.set_main_option("sqlalchemy.url", url)
    return config


_UNSET = object()


def check(url=_UNSET, script_location=None):
    """(ok, message). Never raises for an expected condition.

    `url` falls back to DATABASE_URL only when it is not given at all. An
    explicit empty string means empty: `url or os.environ[...]` made the
    argument advisory, so a caller passing "" silently got whatever the
    environment held -- which is how a check ends up reporting on a database
    nobody asked about.
    """
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script_location = script_location or os.path.join(repo_root, "alembic")
    if url is _UNSET:
        url = os.environ.get("DATABASE_URL", "")

    if not url:
        return False, "DATABASE_URL is not set, so there is nothing to verify"

    script = ScriptDirectory.from_config(_config(script_location, url))
    expected = set(script.get_heads())

    engine = None
    try:
        engine = create_engine(url)
        with engine.connect() as connection:
            actual = set(MigrationContext.configure(connection).get_current_heads())
    except Exception as exc:  # unreachable, wrong credentials, no such database
        # The message carries the exception text. Checked rather than assumed,
        # on SQLAlchemy 2.0.48, across an unparseable URL, an unreachable host
        # and an unknown driver: none of the three reproduces the password. This
        # runs in a container log, so a leak here would be a leak everywhere.
        return False, f"could not read the schema version: {type(exc).__name__}: {exc}"
    finally:
        # A connection pool per call. The entrypoint calls this once and exits,
        # so it never mattered there -- but it is imported and called
        # repeatedly by the tests, and a helper that leaks under repetition is
        # one nobody can reuse.
        if engine is not None:
            engine.dispose()

    if actual == expected:
        return True, f"schema is at head ({', '.join(sorted(expected)) or 'none'})"

    if not actual:
        return False, (
            "the database has no alembic_version at all. It has never been "
            "migrated, and this container will not migrate it: run "
            "`docker compose run --rm migrate` as a deployment step."
        )

    missing = []
    try:
        # Revisions between what is recorded and what is on disk. Names, not a
        # count -- whoever is reading this needs to know what is about to run.
        missing = [
            revision.revision
            for revision in script.iterate_revisions(expected, actual)
        ]
    except Exception:
        pass

    if missing:
        return False, (
            f"the database is behind by {len(missing)} revision(s): "
            f"{', '.join(reversed(missing))}. This container does not migrate; "
            f"run `docker compose run --rm migrate` before starting it."
        )

    return False, (
        f"the recorded revision {sorted(actual)} is not a head of this "
        f"checkout {sorted(expected)}. The image and the database disagree "
        f"about which history they are on -- this is a deploy of the wrong "
        f"revision, not a missing migration."
    )


def main():
    ok, message = check()
    print(("schema check: " if ok else "REFUSING TO START: ") + message,
          file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
