"""Tests for scheduler-level closed-loop retrain."""
from unittest.mock import patch

def _drift(detected: bool):
    """A drift result shaped like the one `compute_drift` really returns.

    These tests used to patch `app.api.drift.check_drift` with a plain dict.
    That mock agreed with the caller while the real function stopped agreeing:
    the contract migration changed the return to a Pydantic model, and
    `drift_result.get(...)` would have raised AttributeError in production
    without a single test going red. The mock now has the shape the real
    function produces, so the next such change fails here.
    """
    from app.schemas import ModelDriftMeasured

    return ModelDriftMeasured(
        status="ok", drift_detected=detected, window=50,
        observations=100, features={}, reason_code=None,
    )


def test_closed_loop_no_drift():
    from app.scheduler import closed_loop_retrain
    with patch("app.api.drift.compute_drift") as mock_drift,          patch("app.locks.RedisLock") as MockLock:
        MockLock.return_value.acquire.return_value = True
        mock_drift.return_value = _drift(False)
        result = closed_loop_retrain(trigger_source="test")
    assert result["status"] == "ok"
    assert result["drift_detected"] is False
    assert result["retrained"] is False


def test_closed_loop_drift_promote():
    from app.scheduler import closed_loop_retrain
    with patch("app.api.drift.compute_drift") as mock_drift,          patch("app.api.retrain._do_retrain") as mock_retrain,          patch("app.api.retrain._get_current_metrics") as mock_metrics,          patch("app.locks.RedisLock") as MockLock,          patch("app.scheduler._start_retrain_log", return_value=99),          patch("app.scheduler._finish_retrain_log"):
        MockLock.return_value.acquire.return_value = True
        mock_drift.return_value = _drift(True)
        mock_metrics.return_value = {"roc_auc": 0.95}
        mock_retrain.return_value = {"status": "success", "metrics": {"roc_auc": 0.96}}
        result = closed_loop_retrain(trigger_source="test")
    assert result["drift_detected"] is True
    assert result["retrained"] is True
    assert result["promoted"] is True
    assert result["new_auc"] == 0.96


def test_closed_loop_drift_reject():
    from app.scheduler import closed_loop_retrain
    with patch("app.api.drift.compute_drift") as mock_drift,          patch("app.api.retrain._do_retrain") as mock_retrain,          patch("app.api.retrain._get_current_metrics") as mock_metrics,          patch("app.locks.RedisLock") as MockLock,          patch("app.scheduler._start_retrain_log", return_value=99),          patch("app.scheduler._finish_retrain_log"):
        MockLock.return_value.acquire.return_value = True
        mock_drift.return_value = _drift(True)
        mock_metrics.return_value = {"roc_auc": 0.95}
        mock_retrain.return_value = {"status": "success", "metrics": {"roc_auc": 0.90}}
        result = closed_loop_retrain(trigger_source="test")
    assert result["drift_detected"] is True
    assert result["retrained"] is True
    assert result["promoted"] is False
    assert "degraded" in result["reject_reason"]
