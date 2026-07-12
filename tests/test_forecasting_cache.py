"""Tests for forecast model caching layer."""

import time
import pytest
import pandas as pd
import numpy as np

from app.services.forecasting.cache import ForecastModelCache
from app.services.forecasting.lstm import LSTMForecaster


@pytest.fixture
def cache():
    return ForecastModelCache(max_entries=4, default_ttl=2.0)


@pytest.fixture
def sample_df():
    dates = pd.date_range(start="2026-01-01", periods=100, freq="D")
    y = np.linspace(50, 80, 100) + np.random.normal(0, 2, 100)
    return pd.DataFrame({"ds": dates, "y": y})


def test_cache_miss_on_empty(cache, sample_df):
    result = cache.get_fitted_model("lstm", "score", sample_df)
    assert result is None


def test_cache_store_and_hit(cache, sample_df):
    model = LSTMForecaster(seq_length=30)
    model.fit(sample_df, target_col="y")

    cache.store_fitted_model("lstm", "score", sample_df, model)
    assert cache.size == 1

    cached = cache.get_fitted_model("lstm", "score", sample_df)
    assert cached is model


def test_cache_miss_on_different_data(cache, sample_df):
    model = LSTMForecaster(seq_length=30)
    model.fit(sample_df, target_col="y")
    cache.store_fitted_model("lstm", "score", sample_df, model)

    # Different data should miss
    different_df = sample_df.copy()
    different_df["y"] = different_df["y"] * 2
    result = cache.get_fitted_model("lstm", "score", different_df)
    assert result is None


def test_cache_expiry(cache, sample_df):
    model = LSTMForecaster(seq_length=30)
    model.fit(sample_df, target_col="y")
    cache.store_fitted_model("lstm", "score", sample_df, model, ttl=0.1)

    time.sleep(0.2)
    result = cache.get_fitted_model("lstm", "score", sample_df)
    assert result is None
    assert cache.size == 0


def test_cache_eviction_on_max_entries(cache, sample_df):
    model = LSTMForecaster(seq_length=30)
    model.fit(sample_df, target_col="y")

    for i in range(5):
        df = sample_df.copy()
        df["y"] = df["y"] + i * 10
        cache.store_fitted_model("lstm", f"metric_{i}", df, model)

    assert cache.size == 4


def test_cache_invalidate_all(cache, sample_df):
    model = LSTMForecaster(seq_length=30)
    model.fit(sample_df, target_col="y")
    cache.store_fitted_model("lstm", "score", sample_df, model)
    cache.store_fitted_model("prophet", "score", sample_df, model)

    count = cache.invalidate()
    assert count == 2
    assert cache.size == 0


def test_cache_invalidate_by_model(cache, sample_df):
    model = LSTMForecaster(seq_length=30)
    model.fit(sample_df, target_col="y")
    cache.store_fitted_model("lstm", "score", sample_df, model)
    cache.store_fitted_model("prophet", "score", sample_df, model)

    count = cache.invalidate("lstm")
    assert count == 1
    assert cache.size == 1
