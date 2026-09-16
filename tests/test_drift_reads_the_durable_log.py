"""The drift check reads the durable `predictions_log` table, not the CSV (#281).

`compute_drift` used to read `data/predictions_log.csv`. In production that path
is inside the container's writable layer -- `/app/data` is mounted from nowhere
-- so it was destroyed on every redeploy, rollback and `--force-recreate`. The
same predictions were being written durably to the `predictions_log` table,
which the check never read. Measured on production 2026-09-07: the file did not
exist, the table held four rows, and the closed loop could only ever answer
"not measured".

The fix moves the source of the recent window to the table. The response
contract is unchanged -- the same statuses and reason codes -- so these tests
are about *where the rows come from*:

- the query really reads the table (`test_recent_predictions_reads_the_table`);
- a table that cannot be read is a fault, not "no drift"
  (`test_a_broken_durable_log_is_a_fault...`, and the closed loop declines);
- a wiped CSV no longer changes the verdict -- the acceptance check #281 asks
  for: the same answer a redeploy used to erase
  (`test_a_wiped_csv_no_longer_decides_the_verdict`).
"""
from __future__ import annotations

import importlib

import pandas as pd
import pytest

drift = importlib.import_module("app.api.drift")


def _write_baseline(path, *, budget: int, n: int) -> None:
    header = "budget,co2_reduction,social_impact,duration_months\n"
    rows = "".join(f"{budget + (i % 10)},{50 + (i % 10)},5,12\n" for i in range(n))
    path.write_text(header + rows, encoding="utf-8")


def test_recent_predictions_reads_the_table():
    """The real query, against the real table -- not a mock of it.

    Rows are flushed (not committed) in one session and read back through the
    same session, then rolled back, so nothing is written to the shared CI
    database. This is the one test that runs `_recent_predictions` for real; the
    branch tests below control it, so this is what pins its return type.
    """
    from app.database import PredictionLog, SessionLocal

    db = SessionLocal()
    try:
        sentinel = 987654.0
        for _ in range(12):
            db.add(
                PredictionLog(
                    endpoint="drift-durable-test",
                    budget=sentinel,
                    co2_reduction=5.0,
                    social_impact=5.0,
                    duration_months=12,
                )
            )
        db.flush()  # assigns ids and makes the rows visible to this session

        frame = drift._recent_predictions(window=500, db=db)

        assert list(frame.columns) == drift.COLS, "the frame must line up with the baseline"
        assert int((frame["budget"] == sentinel).sum()) >= 12, (
            "the durable rows this test wrote were not read back from the table"
        )
    finally:
        db.rollback()
        db.close()


def test_a_broken_durable_log_is_a_fault_not_no_drift(monkeypatch, tmp_path):
    """A table that cannot be read is `unavailable`, never a falsy verdict."""
    baseline = tmp_path / "projects.csv"
    _write_baseline(baseline, budget=100, n=60)
    monkeypatch.setattr(drift, "PROJ_CSV", str(baseline))
    monkeypatch.setattr(drift, "_recent_predictions", lambda window=50, db=None: None)

    result = drift.compute_drift(window=50)

    assert result.status == "unavailable"
    assert result.reason_code == "prediction_log_unavailable"
    assert result.drift_detected is None, "false would claim drift was looked for"


def test_the_closed_loop_declines_when_the_durable_log_cannot_be_read(monkeypatch):
    """End to end: a broken durable log makes the loop decline, not retrain.

    The whole point of #274 and #281 together -- a check that could not run is
    not "the model is fine". Runs the real `compute_drift`, with only the table
    read replaced by a fault.
    """
    monkeypatch.setattr(drift, "_recent_predictions", lambda window=50, db=None: None)
    scheduler = importlib.import_module("app.scheduler")

    class FreeLock:
        def __init__(self, **_kw):
            pass

        def acquire(self):
            return True

        def release(self):
            return None

    monkeypatch.setattr("app.locks.RedisLock", FreeLock)

    result = scheduler.closed_loop_retrain(trigger_source="test")

    assert result["retrained"] is False
    assert result["drift_detected"] is None
    assert result["reason"] == "drift_check_unavailable"
    assert result["status"] != "ok"


def test_a_wiped_csv_no_longer_decides_the_verdict(monkeypatch, tmp_path):
    """The acceptance check #281 asks for.

    The CSV `PRED_LOG` points at does not exist -- exactly what a redeploy
    leaves behind -- yet the durable rows produce a measured verdict. On the
    code this replaces, an absent CSV returned `no_log` and the durable rows
    were never consulted.
    """
    # The KS branch logs a drift event synchronously via
    # `from app.mlflow_tracking import log_drift_event`; stub the call so the
    # test never reaches a tracking server (see test_model_drift_contract).
    monkeypatch.setattr("app.mlflow_tracking.log_drift_event", lambda *a, **k: None)

    baseline = tmp_path / "projects.csv"
    _write_baseline(baseline, budget=100, n=60)
    monkeypatch.setattr(drift, "PROJ_CSV", str(baseline))
    monkeypatch.setattr(drift, "PRED_LOG", str(tmp_path / "wiped-by-redeploy.csv"))
    assert not (tmp_path / "wiped-by-redeploy.csv").exists()

    # Durable rows, far from the baseline, so the KS test has something to find.
    recent = pd.DataFrame(
        {
            "budget": [900_000.0] * 40,
            "co2_reduction": [500.0] * 40,
            "social_impact": [9.0] * 40,
            "duration_months": [36] * 40,
        }
    )
    monkeypatch.setattr(drift, "_recent_predictions", lambda window=50, db=None: recent)

    result = drift.compute_drift(window=50)

    assert result.status == "ok", "a wiped CSV must not turn a measurable window into no_log (#281)"
    assert isinstance(result.drift_detected, bool)
    assert result.observations == 40
