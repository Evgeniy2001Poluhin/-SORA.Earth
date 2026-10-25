"""Breaking the model write on purpose, and following the signal all the way out.

#188. The metric and the alert exist; this is the case that proves they fire.
Without it they belong to the class this repository spent two days removing --
a report that cannot fail, green because nothing has ever made it red.

The failure being reproduced is the real one. On 17 July `models/` became
root-owned while the container runs as uid 1000; every retrain trained,
calibrated, scored, and died writing its artefact. For a month:

    retrain_log                     rows recorded, none successful
    sora_retrain_total{success}     never incremented
    sora_retrain_total{failed}      never incremented either -- drift never
                                    fired, so nothing was even attempted
    the operator                    saw no alerts and concluded health

Injected through `pickle.dump` rather than by chmod on a directory. A
permission test that runs as root passes for the wrong reason, and CI has a job
that runs as root.
"""
import pytest


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    """A trainable set and a models directory this test owns."""
    import numpy as np
    import pandas as pd

    import app.api.retrain as retrain

    rng = np.random.default_rng(0)
    n = 60
    pd.DataFrame({
        "budget": rng.integers(50_000, 500_000, n).astype(float),
        "co2_reduction": rng.integers(50, 900, n).astype(float),
        "social_impact": rng.integers(1, 10, n).astype(float),
        "duration_months": rng.integers(3, 36, n).astype(float),
        "success": [i % 2 for i in range(n)],
    }).to_csv(tmp_path / "projects.csv", index=False)

    models = tmp_path / "models"
    models.mkdir()
    monkeypatch.setattr(retrain, "PROJECTS_CSV", str(tmp_path / "projects.csv"))
    monkeypatch.setattr("app.services.retraining.PROJECTS_CSV", str(tmp_path / "projects.csv"))
    monkeypatch.setattr(retrain, "MODELS_DIR", str(models))
    monkeypatch.setattr(retrain, "PRED_LOG", str(tmp_path / "absent.csv"))
    monkeypatch.setattr("app.services.retraining.PRED_LOG", str(tmp_path / "absent.csv"))
    monkeypatch.setattr(retrain, "DATASET_LOCK", str(tmp_path / ".lock"))
    return retrain


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


@pytest.fixture
def broken_write(monkeypatch, dataset):
    """The artefact write refuses, exactly as it did on production."""
    def _refuse(*_a, **_k):
        raise PermissionError(
            "[Errno 13] Permission denied: '/app/models/rf_model_cal.pkl'")

    monkeypatch.setattr(dataset.pickle, "dump", _refuse)
    return dataset


def test_the_run_is_recorded_as_failed(broken_write, log_db):
    """Step one of the chain. The status was already correct before #188 --
    this pins it so the observability built on top has a foundation."""
    from app.database import RetrainLog

    with pytest.raises(Exception):
        broken_write._do_retrain(min_samples=10)

    rows = log_db().query(RetrainLog).all()
    assert [r.status for r in rows] == ["failed"]


def test_no_success_is_recorded(broken_write, log_db):
    """The distinction the whole issue turns on: a run happened, and it was not
    a success. A counter of runs would have shown activity."""
    from app.database import RetrainLog

    with pytest.raises(Exception):
        broken_write._do_retrain(min_samples=10)

    assert log_db().query(RetrainLog).filter(
        RetrainLog.status == "success").count() == 0


def test_the_age_gauge_does_not_move_on_a_failed_run(broken_write, log_db):
    """The metric added in #192, under the failure it exists for.

    A failed run must leave the age where it was. With no success ever
    recorded that is NaN -- not a sentinel, and not a fresh number.
    """
    import math

    from app.api.infra import read_retrain_history

    with pytest.raises(Exception):
        broken_write._do_retrain(min_samples=10)

    age, readable = read_retrain_history()
    assert math.isnan(age), age


def test_a_failed_run_is_not_an_unreadable_history(broken_write, log_db):
    """Before the decision, this failure and an unreadable database produced the
    same -1, and the same page. A failed run is a readable history that records
    a failure; only the read path failing is a fault of the metrics path.
    """
    from app.api.infra import read_retrain_history

    with pytest.raises(Exception):
        broken_write._do_retrain(min_samples=10)

    _, readable = read_retrain_history()
    assert readable is True


def test_a_success_afterwards_does_move_it(dataset, log_db):
    """Otherwise the assertion above is satisfied by a gauge that never moves,
    which is the defect wearing the shape of the fix."""
    from app.api.infra import read_retrain_history

    dataset._do_retrain(min_samples=10)

    age, readable = read_retrain_history()
    assert readable is True
    assert age >= 0 and age < 300


def test_the_unreadable_alert_does_not_fire_on_a_failed_run(broken_write, log_db):
    """The rule that replaced the stale alert, evaluated on the value this
    failure produces. A failed retrain is a fact about the retrain loop, and the
    page is for a read that failed -- so this must stay quiet.
    """
    import os

    import yaml

    from app.api.infra import read_retrain_history

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "grafana", "provisioning", "alerting",
                           "alerts.yml"), encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    rules = {r["uid"]: r for g in doc["groups"] for r in g["rules"]}
    assert "sora-retrain-stale" not in rules
    expr = rules["sora-retrain-history-unreadable"]["data"][0]["model"]["expr"]
    assert expr == 'sora_retrain_history_readable{job="sora-app"} == bool 0'

    with pytest.raises(Exception):
        broken_write._do_retrain(min_samples=10)
    _, readable = read_retrain_history()

    # The expression evaluated on this state: 1 when unreadable, 0 when readable.
    unreadable_signal = 0 if readable else 1
    assert unreadable_signal == 0, (
        "a failed retrain must not page as an unreadable history"
    )
