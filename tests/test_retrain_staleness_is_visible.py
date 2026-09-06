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
    """The function the gauge computes itself from, called directly.

    Not the gauge's stored value: since #188's follow-up it has no stored
    value -- `set_function` evaluates this at collection, which is what stopped
    the metric depending on which endpoint had been called.
    """
    from app.api.infra import retrain_staleness_seconds

    return retrain_staleness_seconds()


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


def test_the_gauge_is_computed_by_the_scrape_itself(monkeypatch):
    """Not pushed by a runner, and not read from a handler nobody scrapes.

    Refreshing it inside /api/v1/metrics/prometheus published a metric that
    read 0.0 on production while a retrain had succeeded four hours earlier:
    infra/prometheus.yml scrapes /metrics at the root, which that handler never
    runs. A gauge depending on which endpoint was called reports on the
    endpoint.

    This asserted that `app/main.py` contained the text
    `set_function(retrain_staleness_seconds)`. Two things were wrong with that.
    It tested the source rather than the behaviour, so it would have passed
    with the wiring present and broken. And `set_function` had to go in #262:
    under `PROMETHEUS_MULTIPROC_DIR` it is accepted and never collected --
    measured -- so the gauge would have vanished from a four-worker scrape
    while this test stayed green. The computation moved into the `/metrics`
    handler; what is asserted now is that scraping performs it.
    """
    from fastapi.testclient import TestClient

    import app.api.infra as infra_module
    import app.metrics_endpoint as endpoint
    from app.main import app

    calls = []

    def fake_staleness():
        calls.append(1)
        return 1234.0

    monkeypatch.setattr(infra_module, "retrain_staleness_seconds", fake_staleness)

    client = TestClient(app)
    body = client.get("/metrics").text

    assert calls, "the scrape did not compute the gauge"
    assert "sora_retrain_seconds_since_success 1234.0" in body, [
        line for line in body.splitlines()
        if line.startswith("sora_retrain_seconds_since_success")
    ]

    # And again, with a different answer: a value cached from the first scrape
    # would satisfy the assertion above forever.
    calls.clear()

    def later():
        calls.append(1)
        return 5678.0

    monkeypatch.setattr(infra_module, "retrain_staleness_seconds", later)
    body = client.get("/metrics").text

    assert calls, "the second scrape reused the first value"
    assert "sora_retrain_seconds_since_success 5678.0" in body
