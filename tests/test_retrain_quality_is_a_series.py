"""Model quality is queryable across runs, not sealed in a JSON string.

The values were always written -- `metrics_json` has carried `roc_auc`,
`test_samples` and the rest since the table existed. What could not be asked is
anything spanning runs: has AUC moved, on how many test rows, is 0.81 against
0.80 inside the noise. The promotion gate compares two point estimates and has
no way to know whether the difference is real.

**A second finding, recorded rather than fixed here.** Measured on production
2026-08-14: `retrain_log` holds 21 rows, all from 2026-07-05. One day, forty
days ago. `MIN_AUC_THRESHOLD = 0.80` has therefore not been reached in forty
days because no retrain was attempted -- which is zero coverage, not a passed
check, and the two look identical from outside.
"""
import json

import pytest


@pytest.fixture
def log_db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import app.database as database

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    database.RetrainLog.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", Session)
    return Session


METRICS = {"accuracy": 0.9708, "f1_score": 0.9756, "roc_auc": 0.9939,
           "train_samples": 680, "test_samples": 171}


def _run(status="success", metrics=METRICS):
    from app.scheduler import _finish_retrain_log, _start_retrain_log

    log_id = _start_retrain_log(trigger_source="test", job_name="t")
    _finish_retrain_log(log_id, status=status, metrics=metrics)
    return log_id


def test_the_score_and_its_sample_size_are_columns(log_db):
    """Both. An AUC without the size it was measured on is a number, and the
    comparison the gate makes on it cannot be a comparison of evidence."""
    from app.database import RetrainLog

    _run()
    db = log_db()
    row = db.query(RetrainLog).one()

    assert row.roc_auc == pytest.approx(0.9939)
    assert row.test_samples == 171
    assert row.train_samples == 680


def test_the_blob_is_still_written(log_db):
    """Kept on purpose: it carries fields with no column yet, and it is what
    makes the migration reversible without a second backfill."""
    from app.database import RetrainLog

    _run()
    stored = json.loads(log_db().query(RetrainLog).one().metrics_json)

    assert stored["roc_auc"] == pytest.approx(0.9939)


def test_the_columns_agree_with_the_blob(log_db):
    """Written from the dict, not parsed back out of the string, so one run
    cannot end up with two different answers about itself."""
    from app.database import RetrainLog

    _run()
    row = log_db().query(RetrainLog).one()
    stored = json.loads(row.metrics_json)

    assert row.roc_auc == pytest.approx(stored["roc_auc"])
    assert row.test_samples == stored["test_samples"]


def test_a_run_that_reports_nothing_leaves_them_null(log_db):
    """A failed run has no metrics. NULL says that; 0.0 would say the model
    scored zero."""
    from app.database import RetrainLog

    _run(status="failed", metrics=None)
    row = log_db().query(RetrainLog).one()

    assert row.roc_auc is None
    assert row.test_samples is None


def test_an_unusable_value_is_null_not_a_crash(log_db):
    """The writer must not take a retrain down over a metric it cannot read."""
    from app.database import RetrainLog

    _run(metrics={"roc_auc": "n/a", "test_samples": None})
    row = log_db().query(RetrainLog).one()

    assert row.roc_auc is None
    assert row.test_samples is None


def test_runs_can_be_compared_across_time(log_db):
    """The point of the change: a query over runs, which a blob cannot answer."""
    from app.database import RetrainLog

    _run(metrics=dict(METRICS, roc_auc=0.80, test_samples=171))
    _run(metrics=dict(METRICS, roc_auc=0.81, test_samples=171))

    scores = [r.roc_auc for r in
              log_db().query(RetrainLog).order_by(RetrainLog.id).all()]

    assert scores == pytest.approx([0.80, 0.81])
