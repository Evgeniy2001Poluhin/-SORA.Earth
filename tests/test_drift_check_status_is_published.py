"""The closed loop publishes what its drift check found, for every outcome.

Decision 2026-10-21 (GPT-RETRAIN-ALERT-DECISION): while drift is unmeasured the
service must say so, not stay silent. Before this, the closed loop logged its
outcome and published nothing, so an alert could not tell "no drift" from "no
check" -- the very distinction `NOT_MEASURED_STATUSES` exists to draw.

Each run now sets one `sora_drift_check_status{status=...}` series to 1 and the
others to 0, and moves `sora_drift_check_last_run_timestamp_seconds`. These tests
run the real `closed_loop_retrain` with only the lock and the retrain stubbed,
and read the gauges back.
"""
from __future__ import annotations

import re
import sys
import types

import pytest


class _Lock:
    def __init__(self, **kw):
        pass

    @staticmethod
    def acquire():
        return True

    @staticmethod
    def release():
        return None


@pytest.fixture()
def loop(monkeypatch):
    monkeypatch.setitem(sys.modules, "app.locks", types.SimpleNamespace(RedisLock=_Lock))

    import app.scheduler as scheduler
    import app.services.retraining as retraining_module

    retrains = []
    monkeypatch.setattr(
        retraining_module, "_do_retrain",
        lambda **kw: retrains.append(kw) or {"status": "ok", "metrics": {}},
        raising=False,
    )
    return types.SimpleNamespace(run=scheduler.closed_loop_retrain, retrains=retrains,
                                 scheduler=scheduler)


def _verdict(monkeypatch, status, drift_detected=None):
    import app.api.drift as drift_module

    value = types.SimpleNamespace(status=status, drift_detected=drift_detected,
                                  reason_code="probe")
    monkeypatch.setattr(drift_module, "compute_drift", lambda window=50: value)
    return value


def _status_series(scheduler):
    """Read the gauge back: {status_label: value}. Reading the child's value is
    what Prometheus would scrape, so this is the behaviour under test."""
    from app.prom_metrics import DRIFT_CHECK_STATUSES

    return {
        s: scheduler.sora_drift_check_status.labels(status=s)._value.get()
        for s in DRIFT_CHECK_STATUSES
    }


def _one_outcome(series):
    """The outcome, read back from the gauge: exactly one series reads 1."""
    lit = [s for s, v in series.items() if v == 1.0]
    assert len(lit) == 1, series
    assert all(v in (0.0, 1.0) for v in series.values()), series
    return lit[0]


@pytest.mark.parametrize("compute_status, expected", [
    ("no_log", "no_log"),
    ("insufficient_data", "insufficient_data"),
    ("unavailable", "unavailable"),
    ("ok", "measured"),
])
def test_each_outcome_is_published_and_only_that_one(loop, monkeypatch,
                                                      compute_status, expected):
    """The status for each branch of the closed loop reads back from the gauge.

    The no_log branch is the one that is live in production: its absence here
    would leave the drift alert reading nothing where it needs a value.
    """
    _verdict(monkeypatch, compute_status, drift_detected=False if compute_status == "ok" else None)
    loop.run(trigger_source="test")

    assert _one_outcome(_status_series(loop.scheduler)) == expected


def test_a_check_that_raises_is_published_as_unavailable(loop, monkeypatch):
    """A raised exception is a check that could not run, not a verdict. It must
    not leave the previous outcome standing."""
    import app.api.drift as drift_module

    def boom(window=50):
        raise RuntimeError("the KS test blew up")

    monkeypatch.setattr(drift_module, "compute_drift", boom)
    result = loop.run(trigger_source="test")

    assert result["status"] == "error"
    assert _one_outcome(_status_series(loop.scheduler)) == "unavailable"


def test_a_later_run_replaces_the_earlier_outcome(loop, monkeypatch):
    """Otherwise the gauge would keep a stale outcome after the check recovers."""
    _verdict(monkeypatch, "no_log")
    loop.run(trigger_source="test")
    assert _one_outcome(_status_series(loop.scheduler)) == "no_log"

    _verdict(monkeypatch, "ok", drift_detected=False)
    loop.run(trigger_source="test")
    assert _one_outcome(_status_series(loop.scheduler)) == "measured"


def test_the_last_run_time_moves_on_every_outcome(loop, monkeypatch):
    """A check that ran and measured nothing is still a check that ran. The
    timestamp is for telling a stopped loop from a quiet one."""
    gauge = loop.scheduler.sora_drift_check_last_run
    gauge.set(0.0)
    _verdict(monkeypatch, "no_log")
    loop.run(trigger_source="test")

    assert gauge._value.get() > 1_000_000_000  # a Unix time, not the zero we set


def test_a_lock_held_run_publishes_nothing(loop, monkeypatch):
    """A skipped run never checked drift, so it must not report an outcome."""
    before = _status_series(loop.scheduler)

    class _Held(_Lock):
        @staticmethod
        def acquire():
            return False

    monkeypatch.setitem(sys.modules, "app.locks", types.SimpleNamespace(RedisLock=_Held))
    result = loop.run(trigger_source="test")

    assert result["reason"] == "lock_held"
    assert _status_series(loop.scheduler) == before


def test_no_log_still_declines_to_retrain(loop, monkeypatch):
    """Publishing the outcome changes nothing about the decision: no drift
    verdict means no retrain."""
    _verdict(monkeypatch, "no_log")
    result = loop.run(trigger_source="test")

    assert result["drift_detected"] is None
    assert loop.retrains == []


def test_the_traffic_term_matches_the_real_evaluate_and_predict_routes():
    """The alert's handler regex is checked against the application's routes, not
    against a copy of the path. Otherwise a renamed route would silently stop
    counting as traffic and the alert would go quiet with the service in use."""
    import os

    import yaml

    from app.main import app

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "grafana", "provisioning", "alerting", "alerts.yml"),
              encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    rule = next(r for g in doc["groups"] for r in g["rules"]
                if r["uid"] == "sora-drift-unmeasured-with-traffic")
    expr = next(d["model"]["expr"] for d in rule["data"] if "expr" in d["model"])
    pattern = re.search(r'handler=~"([^"]+)"', expr).group(1)

    paths = {getattr(r, "path", "") for r in app.routes}
    traffic = {p for p in paths if p.startswith(("/api/v1/evaluate", "/api/v1/predict"))}
    assert "/api/v1/evaluate" in traffic, sorted(p for p in paths if "evaluate" in p)
    assert "/api/v1/predict" in traffic, sorted(p for p in paths if "predict" in p)

    matched = {p for p in paths if re.fullmatch(pattern, p)}
    assert traffic <= matched, traffic - matched
    # Not a catch-all: the drift and metrics routes are not traffic for this alert.
    assert not re.fullmatch(pattern, "/api/v1/drift")
    assert not re.fullmatch(pattern, "/api/v1/metrics")
