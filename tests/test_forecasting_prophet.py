"""Tests for Prophet forecaster."""

import pytest
import pandas as pd
import numpy as np

# Skip all tests if prophet not installed
pytest.importorskip("prophet")

from app.services.forecasting.prophet import ProphetForecaster


@pytest.fixture
def sample_timeseries():
    """Generate synthetic time series with seasonality."""
    dates = pd.date_range(start="2026-01-01", periods=90, freq="D")
    # Trend + seasonal component + noise
    t = np.arange(90)
    y = 70 + 0.1 * t + 5 * np.sin(2 * np.pi * t / 30) + np.random.normal(0, 1, 90)
    df = pd.DataFrame({"ds": dates, "y": y})
    return df


def test_prophet_forecaster_init():
    """Test ProphetForecaster initialization."""
    forecaster = ProphetForecaster()

    assert forecaster.model_name == "Prophet"
    assert forecaster.version == "1.0"
    assert forecaster.model is None


def test_prophet_forecaster_fit(sample_timeseries):
    """Test Prophet fitting."""
    forecaster = ProphetForecaster()
    forecaster.fit(sample_timeseries, target_col="y")

    assert forecaster.model is not None


def test_prophet_forecaster_fit_insufficient_data():
    """Test that Prophet raises error with insufficient data."""
    dates = pd.date_range(start="2026-01-01", periods=5, freq="D")
    y = np.array([1, 2, 3, 4, 5])
    df = pd.DataFrame({"ds": dates, "y": y})

    forecaster = ProphetForecaster()

    with pytest.raises(ValueError, match="Insufficient data"):
        forecaster.fit(df, target_col="y")


@pytest.mark.integration
def test_prophet_forecaster_predict(sample_timeseries):
    """Test Prophet prediction."""
    forecaster = ProphetForecaster()
    forecaster.fit(sample_timeseries, target_col="y")
    result = forecaster.predict(horizon=30)

    assert len(result.dates) == 30
    assert len(result.yhat) == 30
    assert len(result.yhat_lower) == 30
    assert len(result.yhat_upper) == 30
    assert result.model_name == "Prophet"
    assert result.confidence in ["high", "medium", "low"]
    assert "avg_ci_width" in result.metadata


def test_prophet_forecaster_predict_not_fitted():
    """Test that predict raises error if model not fitted."""
    forecaster = ProphetForecaster()

    with pytest.raises(ValueError, match="Model not fitted"):
        forecaster.predict(horizon=7)


@pytest.mark.integration
def test_prophet_forecaster_confidence_intervals(sample_timeseries):
    """Test that Prophet produces valid confidence intervals."""
    forecaster = ProphetForecaster()
    forecaster.fit(sample_timeseries, target_col="y")
    result = forecaster.predict(horizon=14)

    # Check confidence interval ordering
    for i in range(14):
        assert result.yhat_lower[i] <= result.yhat[i] <= result.yhat_upper[i]

    # Check that intervals have non-zero width
    ci_widths = [u - l for u, l in zip(result.yhat_upper, result.yhat_lower)]
    assert all(w > 0 for w in ci_widths)


@pytest.mark.integration
def test_prophet_forecaster_validate(sample_timeseries):
    """Test Prophet validation metrics."""
    forecaster = ProphetForecaster()
    metrics = forecaster.validate(sample_timeseries, target_col="y")

    assert "mae" in metrics
    assert "rmse" in metrics
    assert "mape" in metrics
    assert metrics["mae"] >= 0
    assert metrics["rmse"] >= 0
    assert metrics["mape"] >= 0


def test_prophet_seasonal_decomposition():
    """Test that Prophet captures seasonality."""
    # Create strong seasonal pattern
    dates = pd.date_range(start="2026-01-01", periods=120, freq="D")
    t = np.arange(120)
    y = 50 + 10 * np.sin(2 * np.pi * t / 30)  # 30-day cycle
    df = pd.DataFrame({"ds": dates, "y": y})

    forecaster = ProphetForecaster()
    forecaster.fit(df, target_col="y")
    result = forecaster.predict(horizon=30)

    # Check that predictions follow the pattern (not flat)
    pred_std = np.std(result.yhat)
    assert pred_std > 1.0, "Prophet should capture seasonal variation"
