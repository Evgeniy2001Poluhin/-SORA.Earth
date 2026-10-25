"""How long since a retrain last succeeded, as a metric.

#188. `sora_retrain_total{status=...}` answers "how many runs failed". It
cannot answer "has anything run at all", and that was the question left open
for a month: `models/` became root-owned on 17 July, every retrain died writing
its artefact, and because drift never fired the scheduler never attempted one.

No failures were counted. No failures looked like health.

A gauge of **age**, not a counter of events. It is information. The alert no
longer reads it (decision 2026-10-21): a retrain that was never needed looks
like one that stopped, and the page fired on exactly that. What must page is an
unreadable history, which `sora_retrain_history_readable` reports on its own.

"No success recorded" and "history unreadable" are separate facts and are
returned separately: NaN for the age in both, and the readable flag tells them
apart. Before, both were -1, and that conflation is what these tests pin down.
"""
import math
from datetime import datetime, timedelta, timezone

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as database


@pytest.fixture
def log_db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    database.RetrainLog.__table__.create(engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    return sessionmaker(bind=engine)


def _add(Session, status, hours_ago):
    db = Session()
    try:
        db.add(database.RetrainLog(
            status=status,
            started_at=datetime.utcnow() - timedelta(hours=hours_ago + 1),
            finished_at=datetime.utcnow() - timedelta(hours=hours_ago),
        ))
        db.commit()
    finally:
        db.close()


def _read():
    """The single read the gauges are set from, called directly.

    Not the gauges' stored values: the scrape computes them, so the function
    is the thing under test.
    """
    from app.api.infra import read_retrain_history

    return read_retrain_history()


def _value():
    return _read()[0]


def test_it_reports_the_age_of_the_last_success(log_db):
    _add(log_db, "success", hours_ago=3)

    age, readable = _read()
    assert readable is True
    assert age == pytest.approx(3 * 3600, rel=0.02)


def test_a_failure_does_not_reset_it(log_db):
    """The month of silence looked exactly like this: runs recorded, none of
    them successful."""
    _add(log_db, "success", hours_ago=48)
    _add(log_db, "failed", hours_ago=1)

    assert _value() == pytest.approx(48 * 3600, rel=0.02)


def test_no_success_recorded_is_nan_and_readable(log_db):
    """A failed run is recorded, so the history is read and says: no success.
    The age is NaN, and the history is readable -- this is information, not a
    fault."""
    _add(log_db, "failed", hours_ago=1)

    age, readable = _read()
    assert math.isnan(age)
    assert readable is True


def test_an_empty_table_is_nan_and_readable(log_db):
    age, readable = _read()
    assert math.isnan(age)
    assert readable is True


def test_the_most_recent_success_wins(log_db):
    _add(log_db, "success", hours_ago=100)
    _add(log_db, "success", hours_ago=2)

    assert _value() == pytest.approx(2 * 3600, rel=0.02)


def test_an_unreadable_database_is_nan_and_not_readable(monkeypatch):
    """The read failed. The age must not be a sentinel that could be mistaken
    for a real value, and readability must say the read failed -- the alert
    reads that flag, not the age."""
    import app.database as db_module

    def _boom():
        raise RuntimeError("database is gone")

    monkeypatch.setattr(db_module, "SessionLocal", _boom)

    age, readable = _read()
    assert math.isnan(age)
    assert readable is False


def test_an_unreadable_history_is_not_the_same_as_no_success(log_db, monkeypatch):
    """The conflation this change removes. Same age (NaN) in both cases; only
    readability separates them, and the page must follow readability."""
    import app.database as db_module

    _add(log_db, "failed", hours_ago=1)
    _, readable_empty = _read()

    def _boom():
        raise RuntimeError("database is gone")

    monkeypatch.setattr(db_module, "SessionLocal", _boom)
    _, readable_broken = _read()

    assert readable_empty is True
    assert readable_broken is False


def test_the_gauges_are_set_from_one_read_at_scrape(monkeypatch):
    """Not pushed by a runner, and not read from a handler nobody scrapes.

    Refreshing it inside /api/v1/metrics/prometheus published a metric that
    read 0.0 on production while a retrain had succeeded four hours earlier:
    infra/prometheus.yml scrapes /metrics at the root, which that handler never
    runs. A gauge depending on which endpoint was called reports on the
    endpoint.

    Both gauges are set in the scrape's own refresh, from a single call, so the
    age and its readability describe the same moment. Asserted by behaviour:
    a scrape performs the read, and what it returns is what appears.
    """
    from fastapi.testclient import TestClient

    import app.api.infra as infra_module
    from app.main import app

    calls = []

    def fake_read():
        calls.append(1)
        return 1234.0, True

    monkeypatch.setattr(infra_module, "read_retrain_history", fake_read)

    client = TestClient(app)
    body = client.get("/metrics").text

    assert calls, "the scrape did not compute the gauges"
    assert "sora_retrain_seconds_since_success 1234.0" in body, [
        line for line in body.splitlines()
        if line.startswith("sora_retrain_seconds_since_success")
    ]
    assert "sora_retrain_history_readable 1.0" in body

    # And again, with a different answer: a value cached from the first scrape
    # would satisfy the assertion above forever.
    calls.clear()

    def later():
        calls.append(1)
        return float("nan"), False

    monkeypatch.setattr(infra_module, "read_retrain_history", later)
    body = client.get("/metrics").text

    assert calls, "the second scrape reused the first value"
    assert "sora_retrain_history_readable 0.0" in body
    assert "sora_retrain_seconds_since_success NaN" in body


def test_a_failed_read_at_scrape_reports_unreadable(monkeypatch):
    """If the read path raises before it can answer, the scrape still succeeds
    and reports the history as unreadable, rather than leaving the last good
    value in place."""
    from fastapi.testclient import TestClient

    import app.api.infra as infra_module
    from app.main import app

    def boom():
        raise RuntimeError("import or query failed")

    monkeypatch.setattr(infra_module, "read_retrain_history", boom)
    body = TestClient(app).get("/metrics").text

    assert "sora_retrain_history_readable 0.0" in body
