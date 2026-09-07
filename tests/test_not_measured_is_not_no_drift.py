""""Not measured" and "measured, no drift" are different answers (#274).

#251 taught both callers of `compute_drift` to handle `unavailable`: a check
that could not run is not "no drift". Two statuses of the same kind were left
behind. `no_log` and `insufficient_data` carry `drift_detected=None` -- and
`ModelDriftNotMeasured` says why in as many words:

    `drift_detected` is null and not false: false would assert that drift was
    looked for and not found.

Both callers then did `bool(drift.drift_detected)`, turning that null into
False and reporting `reason: "drift_not_detected"` -- the assertion the schema
forbids.

Observed on production, not deduced. The daily run of 2026-09-07 03:00:00.044Z
logged:

    Closed loop: no drift, skipping retrain

while `/app/data/predictions_log.csv` did not exist, so nothing had been
compared with anything.
"""
from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: The handler is called directly, so the `Depends(require_admin)` guard never
#: runs; the value only has to be something. Authorisation is a different
#: test's subject and stubbing it here would hide nothing.
_ADMIN = {"username": "test-admin", "role": "admin"}


@pytest.fixture()
def loop(monkeypatch):
    """The closed loop with its lock and retrain replaced; the drift verdict is
    the input under test and is not stubbed away."""
    class _Lock:
        def __init__(self, **kw):
            pass

        @staticmethod
        def acquire():
            return True

        @staticmethod
        def release():
            return None

    monkeypatch.setitem(sys.modules, "app.locks", types.SimpleNamespace(RedisLock=_Lock))

    import app.api.retrain as retrain_module
    import app.scheduler as scheduler

    retrains = []
    monkeypatch.setattr(
        retrain_module, "_do_retrain",
        lambda **kw: retrains.append(kw) or {"status": "ok", "metrics": {}},
        raising=False,
    )
    return types.SimpleNamespace(run=scheduler.closed_loop_retrain, retrains=retrains)


def _verdict(monkeypatch, status, drift_detected):
    import app.api.drift as drift_module

    value = types.SimpleNamespace(
        status=status, drift_detected=drift_detected, reason_code="probe")
    monkeypatch.setattr(drift_module, "compute_drift", lambda window=50: value)
    return value


@pytest.mark.parametrize("status", ["no_log", "insufficient_data"])
def test_the_closed_loop_reports_not_measured_rather_than_no_drift(loop, monkeypatch, status):
    _verdict(monkeypatch, status, None)

    result = loop.run(trigger_source="test")

    assert result["drift_detected"] is None, (
        "None means nobody looked; False asserts that somebody did"
    )
    assert result["reason"] == "drift_not_measured"
    assert result["drift_status"] == status
    assert result["retrained"] is False
    assert loop.retrains == []


def test_a_measured_absence_of_drift_still_says_so(loop, monkeypatch):
    """The other half. Without this, returning None for everything would pass."""
    _verdict(monkeypatch, "ok", False)

    result = loop.run(trigger_source="test")

    assert result["drift_detected"] is False
    assert result["reason"] == "drift_not_detected"
    assert result["status"] == "ok"


def test_an_unavailable_check_keeps_its_own_reason(loop, monkeypatch):
    """A fault and a legitimate absence of data are both "not measured", and an
    operator needs to tell them apart: one is something to fix."""
    _verdict(monkeypatch, "unavailable", None)

    result = loop.run(trigger_source="test")

    assert result["drift_detected"] is None
    assert result["reason"] == "drift_check_unavailable"


def test_measured_drift_still_retrains(loop, monkeypatch):
    """The path that must not have been touched."""
    _verdict(monkeypatch, "ok", True)

    result = loop.run(trigger_source="test")

    assert loop.retrains, "a measured drift no longer triggers a retrain"
    assert result.get("drift_detected") is True


# --- the other caller ------------------------------------------------------


@pytest.mark.parametrize("status", ["no_log", "insufficient_data"])
def test_the_auto_retrain_endpoint_makes_the_same_distinction(monkeypatch, status):
    """`POST /mlops/auto-retrain` had the identical defect.

    CLAUDE.md already records that these two paths applied different rules once
    before; fixing one and not the other would put them back in that state.
    """
    import app.api.drift as drift_module
    import app.api.infra as infra

    class _Drift:
        def __init__(self, status):
            self.status = status
            self.drift_detected = None

        def model_dump(self):
            return {"status": self.status, "drift_detected": None}

    monkeypatch.setattr(drift_module, "compute_drift", lambda window=50: _Drift(status))

    result = infra.auto_retrain_on_drift(
        window=50, min_samples=20, force=False, current_user=_ADMIN)

    assert result["drift_detected"] is None
    assert result["reason"] == "drift_not_measured"
    assert result["retrained"] is False


def test_force_still_overrides_an_absent_verdict(monkeypatch):
    """`force` means "retrain regardless of the verdict", and the absence of one
    is not an exception to that -- it behaves as it already did for
    `unavailable`."""
    import app.api.drift as drift_module
    import app.api.infra as infra
    import app.api.retrain as retrain_module

    class _Drift:
        status = "no_log"
        drift_detected = None

        def model_dump(self):
            return {"status": "no_log"}

    calls = []
    monkeypatch.setattr(drift_module, "compute_drift", lambda window=50: _Drift())
    monkeypatch.setattr(retrain_module, "_do_retrain",
                        lambda **kw: calls.append(kw) or {"status": "ok", "metrics": {}})
    monkeypatch.setattr(retrain_module, "_get_current_metrics", lambda: {"auc_roc": 0.9})

    infra.auto_retrain_on_drift(
        window=50, min_samples=20, force=True, current_user=_ADMIN)

    assert calls, "force no longer overrides an absent verdict"


# --- the guard -------------------------------------------------------------


def test_every_caller_that_acts_on_a_verdict_handles_the_absent_ones():
    """Structural, because this is the second time the same omission shipped.

    #251 fixed `unavailable` in both callers and left these two behind in both.
    A third caller would repeat it, so the rule is checked rather than
    remembered: anything that calls `compute_drift` and then branches on the
    result must mention `NOT_MEASURED_STATUSES`.
    """
    offenders = []
    for path in (ROOT / "app").rglob("*.py"):
        if path.name == "drift.py":
            continue
        source = path.read_text()
        if "compute_drift(" not in source:
            continue
        tree = ast.parse(source)
        acts = any(
            isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "bool"
            for node in ast.walk(tree)
        )
        if acts and "NOT_MEASURED_STATUSES" not in source:
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == [], (
        "these call compute_drift and coerce the verdict without handling the "
        f"statuses that carry no verdict: {offenders}"
    )


def test_the_guard_can_find_a_violation():
    """Negative control: an empty offender list is what a broken scan gives too."""
    source = "from app.api.drift import compute_drift\nd = compute_drift()\nx = bool(d.drift_detected)\n"
    tree = ast.parse(source)

    acts = any(isinstance(n, ast.Call) and getattr(n.func, "id", None) == "bool"
               for n in ast.walk(tree))

    assert acts and "NOT_MEASURED_STATUSES" not in source
