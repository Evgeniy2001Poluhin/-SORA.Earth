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
    assert "retrain_log" in message, message
    assert "does not create tables" in message, message


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
