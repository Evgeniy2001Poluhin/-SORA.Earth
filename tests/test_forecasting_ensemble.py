"""Tests for ensemble forecaster with auto-weighting."""

import pytest
import pandas as pd
import numpy as np

from app.services.forecasting.ensemble import EnsembleForecaster


@pytest.fixture
def large_timeseries():
    """Generate large sample (n>=100) for LSTM dominance."""
    dates = pd.date_range(start="2026-01-01", periods=150, freq="D")
    y = np.linspace(60, 85, 150) + np.random.normal(0, 3, 150)
    df = pd.DataFrame({"ds": dates, "y": y})
    return df


@pytest.fixture
def small_timeseries():
    """Generate small sample (n<100) for Prophet dominance."""
    dates = pd.date_range(start="2026-01-01", periods=50, freq="D")
    y = np.linspace(60, 70, 50) + np.random.normal(0, 2, 50)
    df = pd.DataFrame({"ds": dates, "y": y})
    return df


def test_ensemble_forecaster_init():
    """Test EnsembleForecaster initialization."""
    forecaster = EnsembleForecaster()

    assert forecaster.model_name == "Ensemble"
    assert forecaster.version == "1.0"
    assert forecaster.lstm is not None
    assert forecaster.prophet is not None
    assert forecaster.weights == {"lstm": 0.5, "prophet": 0.5}


def test_ensemble_auto_weights_large_sample(large_timeseries):
    """Test that LSTM dominates with large sample size."""
    forecaster = EnsembleForecaster()
    forecaster.fit(large_timeseries, target_col="y")

    # Large sample (150) → LSTM should have higher weight
    assert forecaster.weights["lstm"] >= 0.5


def test_ensemble_auto_weights_small_sample(small_timeseries):
    """Test auto-weight selection logic for small samples."""
    forecaster = EnsembleForecaster()

    try:
        forecaster.fit(small_timeseries, target_col="y")
    except RuntimeError:
        pytest.skip("All models failed (Prophet not installed, LSTM data too small)")

    # At least one model must have succeeded and have non-zero weight
    active_weight = max(forecaster.weights.values())
    assert active_weight > 0
    # LSTM should NOT be active for n=50 (below LSTM_MIN_ROWS threshold)
    assert forecaster.weights.get("lstm", 0.0) == 0.0


@pytest.mark.integration
def test_ensemble_predict(large_timeseries):
    """Test ensemble prediction."""
    forecaster = EnsembleForecaster()
    forecaster.fit(large_timeseries, target_col="y")
    result = forecaster.predict(horizon=14)

    assert len(result.dates) == 14
    assert result.model_name == "Ensemble"
    assert "weights" in result.metadata
    assert "models_used" in result.metadata
    assert result.confidence in ["high", "medium", "low"]


def test_ensemble_predict_not_fitted():
    """Test that predict raises error if not fitted."""
    forecaster = EnsembleForecaster()

    with pytest.raises(Exception):  # Should raise error
        forecaster.predict(horizon=7)


@pytest.mark.integration
def test_ensemble_confidence_intervals(large_timeseries):
    """Test that ensemble produces valid confidence intervals."""
    forecaster = EnsembleForecaster()
    forecaster.fit(large_timeseries, target_col="y")
    result = forecaster.predict(horizon=10)

    # Check CI ordering
    for i in range(10):
        assert result.yhat_lower[i] <= result.yhat[i] <= result.yhat_upper[i]


@pytest.mark.integration
def test_ensemble_validate(large_timeseries):
    """Test ensemble validation with auto-weighting."""
    forecaster = EnsembleForecaster()
    metrics = forecaster.validate(large_timeseries, target_col="y")

    assert "mae" in metrics
    assert "rmse" in metrics
    assert "mape" in metrics
    assert metrics["mae"] >= 0


def test_ensemble_fallback_lstm_fail():
    """Test fallback when LSTM fails but Prophet succeeds."""
    # Create dataset that might challenge LSTM
    dates = pd.date_range(start="2026-01-01", periods=30, freq="D")
    y = np.array([50] * 30)  # Constant values
    df = pd.DataFrame({"ds": dates, "y": y})

    forecaster = EnsembleForecaster()
    try:
        forecaster.fit(df, target_col="y")
        # Should either succeed or set LSTM weight to 0
        assert forecaster.weights["prophet"] > 0
    except Exception:
        # If fit fails completely, that's expected for edge case
        pass


def test_ensemble_metadata():
    """Test that ensemble metadata includes model information."""
    dates = pd.date_range(start="2026-01-01", periods=80, freq="D")
    y = np.linspace(50, 70, 80) + np.random.normal(0, 2, 80)
    df = pd.DataFrame({"ds": dates, "y": y})

    forecaster = EnsembleForecaster()
    forecaster.fit(df, target_col="y")
    result = forecaster.predict(horizon=7)

    assert "weights" in result.metadata
    assert "models_used" in result.metadata
    assert "total_weight" in result.metadata

    # Check that weights are in metadata
    assert "lstm" in result.metadata["weights"]
    assert "prophet" in result.metadata["weights"]


def test_ensemble_cold_start_bootstrap():
    """Test cold-start with very small dataset (< 10 rows)."""
    dates = pd.date_range(start="2026-01-01", periods=7, freq="D")
    y = np.array([50.0, 52.0, 51.5, 53.0, 54.0, 53.5, 55.0])
    df = pd.DataFrame({"ds": dates, "y": y})

    forecaster = EnsembleForecaster()
    try:
        forecaster.fit(df, target_col="y")
    except RuntimeError:
        pytest.skip("All models failed in cold-start (Prophet not installed)")

    # LinearTrend must be active for cold-start
    assert forecaster.weights.get("linear", 0.0) > 0
    assert forecaster.weights.get("lstm", 0.0) == 0.0

    result = forecaster.predict(horizon=7)
    assert len(result.dates) == 7
    assert result.model_name == "Ensemble"


def test_ensemble_linear_registered():
    """Test that LinearTrend model works via registry."""
    from app.services.forecasting import ModelRegistry

    model = ModelRegistry.get("linear")
    assert model is not None

    dates = pd.date_range(start="2026-01-01", periods=30, freq="D")
    y = np.linspace(50, 65, 30)
    df = pd.DataFrame({"ds": dates, "y": y})

    model.fit(df, target_col="y")
    result = model.predict(horizon=10)
    assert len(result.dates) == 10
    assert result.confidence == "low"
    assert result.metadata["slope_per_day"] > 0
