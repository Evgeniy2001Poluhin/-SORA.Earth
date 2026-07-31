"""`data_refresh_log.started_at` / `finished_at`: aware column, aware comparison.

Two separate faults sat here, and only one of them is about the declaration:

  * the models called these columns naive while migration 0d88c3e3c633 has
    declared them `sa.DateTime(timezone=True)` all along;
  * the liveness query in app/api/admin_snapshot.py built its cutoff with
    `datetime.utcnow()`, which is naive. PostgreSQL reads a naive value bound
    against a timestamptz column in the session's TimeZone, so the 48-hour
    window moved with it. Declaring the column aware does not fix that -- the
    comparison has to carry a timezone.

Measured on PostgreSQL 16, rows at 09:00Z and 11:00Z, a cutoff meaning 10:00Z:

    TimeZone=UTC             naive -> 1 row    aware -> 1 row
    TimeZone=Europe/Moscow   naive -> 2 rows   aware -> 1 row

The server runs UTC, which is why nothing had gone wrong yet.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

DATABASE_URL = os.getenv("DATABASE_URL", "")
requires_postgres = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="the session TimeZone behaviour under test is PostgreSQL's",
)

CUTOFF = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
BEFORE = datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc)
AFTER = datetime(2026, 7, 31, 11, 0, tzinfo=timezone.utc)


def _scratch_postgres():
    url = make_url(DATABASE_URL)
    name = "sora_tz_%s" % uuid.uuid4().hex[:12]
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text('CREATE DATABASE "%s"' % name))
    return admin, name, url.set(database=name)


def _drop(admin, name):
    with admin.connect() as conn:
        conn.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :n AND pid <> pg_backend_pid()"), {"n": name})
        conn.execute(text('DROP DATABASE IF EXISTS "%s"' % name))
    admin.dispose()


@requires_postgres
@pytest.mark.parametrize("session_timezone", ["UTC", "Europe/Moscow"])
def test_the_cutoff_selects_the_same_rows_whatever_the_session_timezone(session_timezone):
    """The window must not depend on how the session happens to be configured."""
    from app.database import Base, DataRefreshLog

    admin, name, url = _scratch_postgres()
    try:
        engine = create_engine(url)
        Base.metadata.create_all(bind=engine)
        with Session(engine) as s:
            s.add(DataRefreshLog(status="ok", started_at=BEFORE))
            s.add(DataRefreshLog(status="ok", started_at=AFTER))
            s.commit()
        engine.dispose()

        engine = create_engine(url, connect_args={"options": "-c TimeZone=%s" % session_timezone})
        with Session(engine) as s:
            selected = s.query(DataRefreshLog).filter(
                DataRefreshLog.started_at > CUTOFF).count()
        engine.dispose()
    finally:
        _drop(admin, name)

    assert selected == 1, (
        "with TimeZone=%s the aware cutoff selected %d row(s); it must always be "
        "the one row after 10:00Z" % (session_timezone, selected)
    )


@requires_postgres
def test_a_naive_cutoff_is_what_moved_the_window():
    """The negative control: the bug, reproduced, so the fix is not a coincidence.

    Without this the test above would pass just as well against code that had
    never been wrong.
    """
    from app.database import Base, DataRefreshLog

    admin, name, url = _scratch_postgres()
    try:
        engine = create_engine(url)
        Base.metadata.create_all(bind=engine)
        with Session(engine) as s:
            s.add(DataRefreshLog(status="ok", started_at=BEFORE))
            s.add(DataRefreshLog(status="ok", started_at=AFTER))
            s.commit()
        engine.dispose()

        naive_cutoff = CUTOFF.replace(tzinfo=None)
        counts = {}
        for session_timezone in ("UTC", "Europe/Moscow"):
            engine = create_engine(
                url, connect_args={"options": "-c TimeZone=%s" % session_timezone})
            with Session(engine) as s:
                counts[session_timezone] = s.query(DataRefreshLog).filter(
                    DataRefreshLog.started_at > naive_cutoff).count()
            engine.dispose()
    finally:
        _drop(admin, name)

    assert counts["UTC"] != counts["Europe/Moscow"], (
        "the naive cutoff was expected to move with the session TimeZone, but "
        "selected %r either way -- if PostgreSQL stopped behaving this way the "
        "test above no longer proves anything" % counts
    )


@requires_postgres
def test_postgresql_returns_aware_datetimes():
    from app.database import Base, DataRefreshLog

    admin, name, url = _scratch_postgres()
    try:
        engine = create_engine(url)
        Base.metadata.create_all(bind=engine)
        with Session(engine) as s:
            s.add(DataRefreshLog(status="ok", started_at=BEFORE, finished_at=AFTER))
            s.commit()
        with Session(engine) as s:
            row = s.query(DataRefreshLog).first()
            started, finished = row.started_at, row.finished_at
        engine.dispose()
    finally:
        _drop(admin, name)

    assert started.tzinfo is not None, "started_at came back naive from PostgreSQL"
    assert finished.tzinfo is not None, "finished_at came back naive from PostgreSQL"
    assert started == BEFORE and finished == AFTER


def test_sqlite_returns_naive_datetimes(tmp_path):
    """Stated, not assumed: SQLite does not carry an offset, and the suite runs on it.

    Code that compares a value read here against an aware datetime raises
    TypeError, so the difference is worth knowing rather than discovering.
    """
    from app.database import Base, DataRefreshLog

    engine = create_engine("sqlite:///%s" % (tmp_path / "tz.db"))
    Base.metadata.create_all(bind=engine)
    with Session(engine) as s:
        s.add(DataRefreshLog(status="ok", started_at=BEFORE))
        s.commit()
    with Session(engine) as s:
        started = s.query(DataRefreshLog).first().started_at
    engine.dispose()

    assert started.tzinfo is None, (
        "SQLite returned an aware datetime (%r); the note above is wrong and the "
        "comparison rules here need revisiting" % started
    )


@requires_postgres
def test_the_api_serialises_the_timestamps():
    """An aware datetime must still come out of the endpoint as JSON.

    Changing the column type is the kind of change that breaks serialisation
    somewhere downstream rather than at the query.
    """
    import json

    from app.database import Base, DataRefreshLog

    admin, name, url = _scratch_postgres()
    try:
        engine = create_engine(url)
        Base.metadata.create_all(bind=engine)
        with Session(engine) as s:
            s.add(DataRefreshLog(status="ok", started_at=BEFORE, finished_at=AFTER))
            s.commit()
        with Session(engine) as s:
            row = s.query(DataRefreshLog).first()
            payload = {
                "started_at": row.started_at.isoformat(),
                "finished_at": row.finished_at.isoformat(),
            }
        engine.dispose()
    finally:
        _drop(admin, name)

    encoded = json.dumps(payload)
    assert "+00:00" in encoded, "the offset was lost on the way out: %s" % encoded
    assert json.loads(encoded)["started_at"].startswith("2026-07-31T09:00")
