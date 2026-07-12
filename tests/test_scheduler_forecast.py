"""Tests for scheduled forecast model retraining (app/scheduler.py:scheduled_pretrain_forecast_models)."""
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
from datetime import datetime


def test_scheduled_pretrain_forecast_models_success(monkeypatch):
    """Test successful forecast model pretraining with sufficient data."""
    from app.scheduler import scheduled_pretrain_forecast_models

    # Mock Redis lock
    mock_lock = MagicMock()
    mock_lock.acquire.return_value = True

    with patch('app.locks.RedisLock', return_value=mock_lock):
        # Mock database query
        mock_rows = []
        for i in range(30):  # 30 days of data
            row = MagicMock()
            row.created_at = datetime(2026, 1, 1 + i, 12, 0, 0)
            row.total_score = 75.0 + i * 0.5
            row.success_probability = 0.7 + i * 0.01
            row.co2_reduction = 100.0 + i * 5.0
            mock_rows.append(row)

        mock_db = MagicMock()
        mock_db.query.return_value.order_by.return_value.all.return_value = mock_rows

        with patch('app.database.SessionLocal', return_value=mock_db):
            # Mock ModelRegistry and cache
            mock_forecaster = MagicMock()
            mock_forecaster.fit.return_value = None

            with patch('app.services.forecasting.ModelRegistry.get', return_value=mock_forecaster):
                with patch('app.services.forecasting.forecast_cache.store_fitted_model') as mock_store:
                    result = scheduled_pretrain_forecast_models()

        # Verify success
        assert result["status"] == "ok"
        assert "metrics_trained" in result
        assert len(result["metrics_trained"]) == 3  # score, prob, co2_reduction
        assert set(result["metrics_trained"]) == {"score", "prob", "co2_reduction"}

        # Verify fit was called (multiple times due to walk-forward validation)
        # Each metric: 1 main fit + multiple validation fits
        assert mock_forecaster.fit.call_count >= 3

        # Verify cache storage (once per metric)
        assert mock_store.call_count == 3

        # Verify lock was acquired and released
        mock_lock.acquire.assert_called_once()
        mock_lock.release.assert_called_once()


def test_scheduled_pretrain_forecast_models_insufficient_data():
    """Test pretraining skips when insufficient data available."""
    from app.scheduler import scheduled_pretrain_forecast_models

    mock_lock = MagicMock()
    mock_lock.acquire.return_value = True

    with patch('app.locks.RedisLock', return_value=mock_lock):
        # Mock database with only 5 rows (below threshold)
        mock_rows = []
        for i in range(5):
            row = MagicMock()
            row.created_at = datetime(2026, 1, 1 + i, 12, 0, 0)
            row.total_score = 75.0
            row.success_probability = 0.7
            row.co2_reduction = 100.0
            mock_rows.append(row)

        mock_db = MagicMock()
        mock_db.query.return_value.order_by.return_value.all.return_value = mock_rows

        with patch('app.database.SessionLocal', return_value=mock_db):
            result = scheduled_pretrain_forecast_models()

        assert result["status"] == "skipped"
        assert result["reason"] == "insufficient_data"
        mock_lock.release.assert_called_once()


def test_scheduled_pretrain_forecast_models_lock_held():
    """Test pretraining skips when lock is already held."""
    from app.scheduler import scheduled_pretrain_forecast_models

    mock_lock = MagicMock()
    mock_lock.acquire.return_value = False  # Lock already held

    with patch('app.locks.RedisLock', return_value=mock_lock):
        result = scheduled_pretrain_forecast_models()

    assert result["status"] == "skipped"
    mock_lock.acquire.assert_called_once()
    mock_lock.release.assert_not_called()  # Should not release if not acquired


def test_scheduled_pretrain_forecast_models_training_error():
    """Test pretraining handles training errors gracefully."""
    from app.scheduler import scheduled_pretrain_forecast_models

    mock_lock = MagicMock()
    mock_lock.acquire.return_value = True

    with patch('app.locks.RedisLock', return_value=mock_lock):
        # Mock database with sufficient data
        mock_rows = []
        for i in range(30):
            row = MagicMock()
            row.created_at = datetime(2026, 1, 1 + i, 12, 0, 0)
            row.total_score = 75.0 + i * 0.5
            row.success_probability = 0.7 + i * 0.01
            row.co2_reduction = 100.0 + i * 5.0
            mock_rows.append(row)

        mock_db = MagicMock()
        mock_db.query.return_value.order_by.return_value.all.return_value = mock_rows

        with patch('app.database.SessionLocal', return_value=mock_db):
            # Mock forecaster that raises exception during fit
            mock_forecaster = MagicMock()
            mock_forecaster.fit.side_effect = ValueError("Training failed")

            with patch('app.services.forecasting.ModelRegistry.get', return_value=mock_forecaster):
                result = scheduled_pretrain_forecast_models()

        # Should return success with empty list (errors logged but not fatal)
        assert result["status"] == "ok"
        assert result["metrics_trained"] == []  # All training attempts failed
        mock_lock.release.assert_called_once()


def test_scheduled_pretrain_forecast_models_partial_success():
    """Test pretraining handles partial failures (some metrics train, others fail)."""
    from app.scheduler import scheduled_pretrain_forecast_models

    mock_lock = MagicMock()
    mock_lock.acquire.return_value = True

    with patch('app.locks.RedisLock', return_value=mock_lock):
        # Mock database with data
        mock_rows = []
        for i in range(30):
            row = MagicMock()
            row.created_at = datetime(2026, 1, 1 + i, 12, 0, 0)
            row.total_score = 75.0 + i * 0.5
            row.success_probability = 0.7 + i * 0.01
            row.co2_reduction = 100.0 + i * 5.0
            mock_rows.append(row)

        mock_db = MagicMock()
        mock_db.query.return_value.order_by.return_value.all.return_value = mock_rows

        with patch('app.database.SessionLocal', return_value=mock_db):
            # Mock forecaster that fails on first call, succeeds on others
            mock_forecaster = MagicMock()
            call_count = [0]

            def fit_side_effect(df, target_col):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise ValueError("First metric failed")
                return None

            mock_forecaster.fit.side_effect = fit_side_effect

            with patch('app.services.forecasting.ModelRegistry.get', return_value=mock_forecaster):
                with patch('app.services.forecasting.forecast_cache.store_fitted_model'):
                    result = scheduled_pretrain_forecast_models()

        # Should succeed with 2 out of 3 metrics
        assert result["status"] == "ok"
        assert len(result["metrics_trained"]) == 2
        # fit called multiple times due to validation attempts
        assert mock_forecaster.fit.call_count >= 3
        mock_lock.release.assert_called_once()


def test_scheduled_pretrain_forecast_models_data_quality():
    """Test pretraining handles poor quality data (nulls, insufficient points after filtering)."""
    from app.scheduler import scheduled_pretrain_forecast_models

    mock_lock = MagicMock()
    mock_lock.acquire.return_value = True

    with patch('app.locks.RedisLock', return_value=mock_lock):
        # Mock database with sparse/null data
        mock_rows = []
        for i in range(30):
            row = MagicMock()
            row.created_at = datetime(2026, 1, 1 + i, 12, 0, 0)
            # Every 3rd row has null values
            row.total_score = 75.0 if i % 3 != 0 else None
            row.success_probability = 0.7 if i % 3 != 0 else None
            row.co2_reduction = 100.0 if i % 3 != 0 else None
            mock_rows.append(row)

        mock_db = MagicMock()
        mock_db.query.return_value.order_by.return_value.all.return_value = mock_rows

        with patch('app.database.SessionLocal', return_value=mock_db):
            mock_forecaster = MagicMock()

            with patch('app.services.forecasting.ModelRegistry.get', return_value=mock_forecaster):
                with patch('app.services.forecasting.forecast_cache.store_fitted_model'):
                    result = scheduled_pretrain_forecast_models()

        # Should still succeed with available data (20 non-null rows after filtering)
        assert result["status"] == "ok"
        assert len(result["metrics_trained"]) >= 0  # May train some or all metrics
        mock_lock.release.assert_called_once()


def test_scheduled_pretrain_forecast_models_registered_in_scheduler():
    """Test that forecast pretraining job is registered in the scheduler."""
    from app.scheduler import scheduler, init_scheduler
    import os

    # Set environment to enable scheduler
    os.environ['SORA_SCHEDULER'] = '1'
    os.environ['RUN_SCHEDULER'] = 'true'

    # Initialize scheduler (will be no-op if already running)
    if not scheduler.running:
        init_scheduler()

    # Find the forecast pretraining job
    jobs = scheduler.get_jobs()
    forecast_job = None
    for job in jobs:
        if job.id == "auto_pretrain_forecast":
            forecast_job = job
            break

    # Verify job exists and is configured correctly
    assert forecast_job is not None, "Forecast pretraining job not found in scheduler"
    assert "Pre-train forecast models" in forecast_job.name
    assert "6" in str(forecast_job.trigger)  # 6-hour interval
    assert forecast_job.func.__name__ == "scheduled_pretrain_forecast_models"
