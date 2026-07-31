"""The application must refuse to start on an incomplete schema.

`init_db()` used to call `Base.metadata.create_all()` when the application was
imported, so a database that migrations had left incomplete was silently
repaired at startup -- which is how four tables came to exist with no migration
behind them (issue #51).

The replacement only reads. These tests assert both halves: that it refuses when
something is missing and says what and how to fix it, and that it stays quiet
when the schema is there. A check that never fires is not a check.
"""

import pytest
from sqlalchemy import create_engine, text


def test_refuses_when_tables_are_missing(tmp_path):
    from app.main import assert_schema_ready

    empty = create_engine("sqlite:///%s" % (tmp_path / "empty.db"))
    with empty.connect() as conn:          # materialise the file; no tables in it
        conn.execute(text("SELECT 1"))

    with pytest.raises(RuntimeError) as excinfo:
        assert_schema_ready(engine=empty)
    empty.dispose()

    message = str(excinfo.value)
    # Actionable, not merely negative: which tables, and what to run.
    assert "alembic upgrade head" in message, message
    assert "missing table: retrain_log" in message, message
    assert "does not create or alter tables" in message, message


def test_refuses_a_stale_schema_whose_table_names_all_match(tmp_path):
    """Names are not the contract.

    A schema left behind by an older revision has every table this application
    expects. A name-only check passes it, the application starts, and the first
    query naming a column added since fails -- at request time, to a user.
    """
    from sqlalchemy import Column, Integer, MetaData, Table
    from app.database import Base
    from app.main import assert_schema_ready

    stale_path = tmp_path / "stale.db"
    stale = create_engine("sqlite:///%s" % stale_path)
    Base.metadata.create_all(bind=stale)
    with stale.begin() as conn:
        conn.execute(text("ALTER TABLE retrain_log DROP COLUMN message"))
    stale.dispose()

    # Re-open so nothing is cached from the create_all above.
    reopened = create_engine("sqlite:///%s" % stale_path)
    with pytest.raises(RuntimeError) as excinfo:
        assert_schema_ready(engine=reopened)
    reopened.dispose()

    message = str(excinfo.value)
    assert "missing column: retrain_log.message" in message, message
    # And it must not claim the table itself is gone -- the table is right there.
    assert "missing table: retrain_log" not in message, message


def test_names_only_what_is_actually_missing(tmp_path):
    """A partial schema must report the gap, not everything."""
    from app.database import Base
    from app.main import assert_schema_ready

    partial = create_engine("sqlite:///%s" % (tmp_path / "partial.db"))
    present = Base.metadata.tables["retrain_log"]
    Base.metadata.create_all(bind=partial, tables=[present])

    with pytest.raises(RuntimeError) as excinfo:
        assert_schema_ready(engine=partial)
    partial.dispose()

    message = str(excinfo.value)
    assert "retrain_log" not in message.split(":", 2)[-1].split("\n")[0], (
        "a table that is present was reported as missing: %s" % message
    )
    assert "batch_results" in message, message


def test_refuses_a_column_the_database_requires_but_the_models_omit(tmp_path):
    """The drift that breaks writes rather than reads.

    Measured on SQLite: with `message` NOT NULL in the database and optional in
    the models, the first insert the ORM issues fails with a not-null violation.
    Every table and every column name is present, so nothing above notices.
    """
    from app.database import Base
    from app.main import assert_schema_ready

    path = tmp_path / "strict.db"
    engine = create_engine("sqlite:///%s" % path)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        # SQLite cannot alter a column, so the table is rebuilt with the stricter
        # nullability -- which is what a hand-edited production table looks like.
        conn.execute(text("DROP TABLE retrain_log"))
        conn.execute(text(
            "CREATE TABLE retrain_log ("
            " id INTEGER NOT NULL PRIMARY KEY, started_at DATETIME NOT NULL,"
            " finished_at DATETIME, duration_sec FLOAT, status VARCHAR(50) NOT NULL,"
            " trigger_source VARCHAR(50), job_name VARCHAR(100),"
            " model_version VARCHAR(100), data_version VARCHAR(100),"
            " metrics_json TEXT, error_message TEXT, message TEXT NOT NULL)"))
    engine.dispose()

    reopened = create_engine("sqlite:///%s" % path)
    with pytest.raises(RuntimeError) as excinfo:
        assert_schema_ready(engine=reopened)
    reopened.dispose()

    message = str(excinfo.value)
    assert "retrain_log.message is NOT NULL" in message, message
    assert "inserts will fail" in message, message


def test_passes_on_a_complete_schema(tmp_path):
    from app.database import Base
    from app.main import assert_schema_ready

    complete = create_engine("sqlite:///%s" % (tmp_path / "complete.db"))
    Base.metadata.create_all(bind=complete)

    assert_schema_ready(engine=complete)   # must not raise
    complete.dispose()


def test_the_application_no_longer_creates_tables_on_import():
    """The property the whole change is about, asserted rather than assumed."""
    import app.database as database

    assert not hasattr(database, "init_db"), (
        "app.database.init_db is back; the application can create its own schema again"
    )
