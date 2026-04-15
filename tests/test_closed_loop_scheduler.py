"""Tests for scheduler-level closed-loop retrain."""
from unittest.mock import patch


def test_closed_loop_no_drift():
    from app.scheduler import closed_loop_retrain
    with patch("app.api.drift.check_drift") as mock_drift,          patch("app.locks.RedisLock") as MockLock:
        MockLock.return_value.acquire.return_value = True
        mock_drift.return_value = {"drift_detected": False, "status": "ok"}
        result = closed_loop_retrain(trigger_source="test")
    assert result["status"] == "ok"
    assert result["drift_detected"] is False
    assert result["retrained"] is False


def test_closed_loop_drift_promote():
    from app.scheduler import closed_loop_retrain
    with patch("app.api.drift.check_drift") as mock_drift,          patch("app.api.retrain._do_retrain") as mock_retrain,          patch("app.api.retrain._get_current_metrics") as mock_metrics,          patch("app.locks.RedisLock") as MockLock,          patch("app.scheduler._start_retrain_log", return_value=99),          patch("app.scheduler._finish_retrain_log"):
        MockLock.return_value.acquire.return_value = True
        mock_drift.return_value = {"drift_detected": True}
        mock_metrics.return_value = {"roc_auc": 0.95}
        mock_retrain.return_value = {"status": "success", "metrics": {"roc_auc": 0.96}}
        result = closed_loop_retrain(trigger_source="test")
    assert result["drift_detected"] is True
    assert result["retrained"] is True
    assert result["promoted"] is True
    assert result["new_auc"] == 0.96


def test_closed_loop_drift_reject():
    from app.scheduler import closed_loop_retrain
    with patch("app.api.drift.check_drift") as mock_drift,          patch("app.api.retrain._do_retrain") as mock_retrain,          patch("app.api.retrain._get_current_metrics") as mock_metrics,          patch("app.locks.RedisLock") as MockLock,          patch("app.scheduler._start_retrain_log", return_value=99),          patch("app.scheduler._finish_retrain_log"):
        MockLock.return_value.acquire.return_value = True
        mock_drift.return_value = {"drift_detected": True}
        mock_metrics.return_value = {"roc_auc": 0.95}
        mock_retrain.return_value = {"status": "success", "metrics": {"roc_auc": 0.90}}
        result = closed_loop_retrain(trigger_source="test")
    assert result["drift_detected"] is True
    assert result["retrained"] is True
    assert result["promoted"] is False
    assert "degraded" in result["reject_reason"]
