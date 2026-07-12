"""Tests for LSTM forecaster with MC Dropout."""

import pytest
import torch
import pandas as pd
import numpy as np
from datetime import datetime

from app.services.forecasting.lstm import LSTMForecaster, LSTMModel


@pytest.fixture
def sample_timeseries():
    """Generate synthetic time series with trend."""
    dates = pd.date_range(start="2026-01-01", periods=100, freq="D")
    y = np.linspace(50, 80, 100) + np.random.normal(0, 2, 100)
    df = pd.DataFrame({"ds": dates, "y": y})
    return df


def test_lstm_model_forward():
    """Test LSTM model forward pass."""
    model = LSTMModel(input_size=5, hidden_size=32, dropout=0.2)
    x = torch.randn(4, 10, 5)  # (batch=4, seq_len=10, features=5)
    out = model(x)

    assert out.shape == (4, 1)
    assert not torch.isnan(out).any()


def test_lstm_model_architecture():
    """Test LSTM model layer structure."""
    model = LSTMModel(input_size=10, hidden_size=64, dropout=0.3)

    # Check that model has correct layers
    assert hasattr(model, "lstm1")
    assert hasattr(model, "lstm2")
    assert hasattr(model, "dropout1")
    assert hasattr(model, "dropout2")
    assert hasattr(model, "fc")

    assert model.hidden_size == 64


def test_lstm_forecaster_init():
    """Test LSTMForecaster initialization."""
    forecaster = LSTMForecaster(seq_length=20, hidden_size=64, dropout=0.1, mc_samples=25)

    assert forecaster.seq_length == 20
    assert forecaster.hidden_size == 64
    assert forecaster.dropout == 0.1
    assert forecaster.mc_samples == 25
    assert forecaster.model_name == "LSTM"
    assert forecaster.version == "1.0"


def test_lstm_forecaster_fit(sample_timeseries):
    """Test LSTM fitting on time series."""
    forecaster = LSTMForecaster(seq_length=10, hidden_size=32, mc_samples=10)
    forecaster.fit(sample_timeseries, target_col="y")

    assert forecaster.model is not None
    assert forecaster.scaler is not None
    assert len(forecaster.feature_cols) > 0
    assert forecaster.last_date is not None


def test_lstm_forecaster_fit_insufficient_data():
    """Test that LSTM raises error with insufficient data."""
    dates = pd.date_range(start="2026-01-01", periods=5, freq="D")
    y = np.array([1, 2, 3, 4, 5])
    df = pd.DataFrame({"ds": dates, "y": y})

    forecaster = LSTMForecaster(seq_length=30)

    with pytest.raises(ValueError, match="Insufficient data"):
        forecaster.fit(df, target_col="y")


@pytest.mark.integration
def test_lstm_forecaster_predict(sample_timeseries):
    """Test LSTM prediction with MC Dropout."""
    forecaster = LSTMForecaster(seq_length=10, hidden_size=32, mc_samples=20)
    forecaster.fit(sample_timeseries, target_col="y")
    result = forecaster.predict(horizon=7)

    assert len(result.dates) == 7
    assert len(result.yhat) == 7
    assert len(result.yhat_lower) == 7
    assert len(result.yhat_upper) == 7
    assert result.model_name == "LSTM"
    assert result.confidence in ["high", "medium", "low"]

    # Check confidence interval ordering
    for i in range(7):
        assert result.yhat_lower[i] <= result.yhat[i] <= result.yhat_upper[i]


def test_lstm_forecaster_predict_not_fitted():
    """Test that predict raises error if model not fitted."""
    forecaster = LSTMForecaster()

    with pytest.raises(ValueError, match="Model not fitted"):
        forecaster.predict(horizon=7)


@pytest.mark.integration
def test_lstm_forecaster_validate(sample_timeseries):
    """Test LSTM validation metrics."""
    forecaster = LSTMForecaster(seq_length=10, hidden_size=32, mc_samples=10)
    metrics = forecaster.validate(sample_timeseries, target_col="y")

    assert "mae" in metrics
    assert "rmse" in metrics
    assert "mape" in metrics
    assert metrics["mae"] >= 0
    assert metrics["rmse"] >= 0
    assert metrics["mape"] >= 0


def test_lstm_mc_dropout_uncertainty():
    """Test that MC Dropout produces variable predictions."""
    dates = pd.date_range(start="2026-01-01", periods=60, freq="D")
    y = np.linspace(50, 70, 60) + np.random.normal(0, 1, 60)
    df = pd.DataFrame({"ds": dates, "y": y})

    forecaster = LSTMForecaster(seq_length=10, hidden_size=32, mc_samples=30)
    forecaster.fit(df, target_col="y")
    result = forecaster.predict(horizon=5)

    # Check that confidence intervals have non-zero width
    ci_widths = [u - l for u, l in zip(result.yhat_upper, result.yhat_lower)]
    assert all(w > 0 for w in ci_widths), "MC Dropout should produce uncertainty"
