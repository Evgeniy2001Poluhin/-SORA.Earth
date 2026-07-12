"""Tests for time series feature engineering."""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from app.services.forecasting.features import FeatureEngineer


@pytest.fixture
def sample_timeseries():
    """Generate synthetic time series data for testing."""
    dates = pd.date_range(start="2026-01-01", periods=100, freq="D")
    y = np.linspace(50, 80, 100) + np.random.normal(0, 2, 100)
    df = pd.DataFrame({"ds": dates, "y": y})
    return df


def test_feature_engineer_init():
    """Test FeatureEngineer initialization."""
    engineer = FeatureEngineer(lookback_window=30)
    assert engineer.lookback == 30


def test_add_lags(sample_timeseries):
    """Test lag feature creation."""
    engineer = FeatureEngineer()
    df_lagged = engineer.add_lags(sample_timeseries, "y", lags=[1, 7])

    assert "y_lag1" in df_lagged.columns
    assert "y_lag7" in df_lagged.columns
    assert df_lagged["y_lag1"].iloc[1] == sample_timeseries["y"].iloc[0]
    assert len(df_lagged) == len(sample_timeseries)


def test_add_rolling_stats(sample_timeseries):
    """Test rolling statistics features."""
    engineer = FeatureEngineer()
    df_rolling = engineer.add_rolling_stats(sample_timeseries, "y", windows=[7])

    assert "y_ma7" in df_rolling.columns
    assert "y_std7" in df_rolling.columns
    # Check that rolling mean is computed
    assert not df_rolling["y_ma7"].isna().all()


def test_add_seasonality(sample_timeseries):
    """Test temporal feature extraction."""
    engineer = FeatureEngineer()
    df_seasonal = engineer.add_seasonality(sample_timeseries, date_col="ds")

    assert "dow" in df_seasonal.columns
    assert "month" in df_seasonal.columns
    assert "quarter" in df_seasonal.columns
    assert "is_weekend" in df_seasonal.columns

    # Verify value ranges
    assert df_seasonal["dow"].min() >= 0
    assert df_seasonal["dow"].max() <= 6
    assert df_seasonal["month"].min() >= 1
    assert df_seasonal["month"].max() <= 12
    assert df_seasonal["quarter"].min() >= 1
    assert df_seasonal["quarter"].max() <= 4
    assert set(df_seasonal["is_weekend"].unique()).issubset({0, 1})


def test_add_external_regressors(sample_timeseries):
    """Test external regressor addition (placeholders without country)."""
    engineer = FeatureEngineer()
    df_external = engineer.add_external_regressors(sample_timeseries)

    assert "carbon_price" in df_external.columns
    assert "gdp_growth" in df_external.columns
    assert "air_quality" in df_external.columns


def test_engineer_all(sample_timeseries):
    """Test full feature engineering pipeline."""
    engineer = FeatureEngineer()
    df_engineered = engineer.engineer_all(sample_timeseries, target_col="y")

    # Check that all feature types are present
    assert "y_lag1" in df_engineered.columns
    assert "y_ma7" in df_engineered.columns
    assert "dow" in df_engineered.columns
    assert "carbon_price" in df_engineered.columns

    # Check that NaN rows from lags are dropped
    assert not df_engineered["y"].isna().any()
    assert len(df_engineered) < len(sample_timeseries)  # Some rows dropped


def test_engineer_all_small_dataset():
    """Test feature engineering on small dataset."""
    dates = pd.date_range(start="2026-01-01", periods=20, freq="D")
    y = np.linspace(50, 60, 20)
    df = pd.DataFrame({"ds": dates, "y": y})

    engineer = FeatureEngineer(lookback_window=10)
    df_engineered = engineer.engineer_all(df, target_col="y")

    # Should still work but with fewer rows after dropping NaNs
    assert len(df_engineered) > 0
    assert len(df_engineered) < len(df)
