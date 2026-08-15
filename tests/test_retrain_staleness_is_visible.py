"""How long since a retrain last succeeded, as a metric.

#188. `sora_retrain_total{status=...}` answers "how many runs failed". It
cannot answer "has anything run at all", and that was the question left open
for a month: `models/` became root-owned on 17 July, every retrain died writing
its artefact, and because drift never fired the scheduler never attempted one.

No failures were counted. No failures looked like health.

A gauge of **age**, not a counter of events, because an alert on age fires when
the system stops trying — the state a counter cannot express.
"""
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


def _value():
    from app.api.infra import _refresh_retrain_staleness
    from app.prom_metrics import sora_retrain_seconds_since_success

    _refresh_retrain_staleness()
    return sora_retrain_seconds_since_success._value.get()


def test_it_reports_the_age_of_the_last_success(log_db):
    _add(log_db, "success", hours_ago=3)

    assert _value() == pytest.approx(3 * 3600, rel=0.02)


def test_a_failure_does_not_reset_it(log_db):
    """The month of silence looked exactly like this: runs recorded, none of
    them successful."""
    _add(log_db, "success", hours_ago=48)
    _add(log_db, "failed", hours_ago=1)

    assert _value() == pytest.approx(48 * 3600, rel=0.02)


def test_never_having_succeeded_is_minus_one(log_db):
    """Not 0, which reads as "one just finished", and not absent, which leaves
    the alert with nothing to compare."""
    _add(log_db, "failed", hours_ago=1)

    assert _value() == -1


def test_an_empty_table_is_minus_one(log_db):
    assert _value() == -1


def test_the_most_recent_success_wins(log_db):
    _add(log_db, "success", hours_ago=100)
    _add(log_db, "success", hours_ago=2)

    assert _value() == pytest.approx(2 * 3600, rel=0.02)


def test_an_unreadable_database_fires_the_alert(monkeypatch):
    """Leaving the previous value would be wrong in a different way: the number
    stops moving, which reads as "recent" to anyone looking at the value rather
    than at its timestamp."""
    import app.database as db_module

    def _boom():
        raise RuntimeError("database is gone")

    monkeypatch.setattr(db_module, "SessionLocal", _boom)

    assert _value() == -1


def test_the_gauge_is_refreshed_when_the_endpoint_is_scraped():
    """Read from the source: the value must not be pushed by whatever last
    retrained, because the failure being watched for is that nothing does."""
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "app", "api", "infra.py"), encoding="utf-8") as fh:
        body = fh.read()

    render = body[body.index("async def prometheus_metrics"):]
    assert "_refresh_retrain_staleness()" in render[:render.index("generate_latest")]
