"""Every forecast interval states its nominal level (C16).

The numeric bounds are not in question here and must not change: this file
pins them against values recorded before the change. What is pinned is the
label. `metadata["interval_level"]` says which nominal level the bounds were
built for -- 0.95 for the linear model, 0.90 for the others -- and the ensemble,
which averages bounds built for different levels, reports None and lists the
levels of the components that contributed.
"""

import numpy as np
import pandas as pd
import pytest

from app.services.forecasting.base import ForecastResult
from app.services.forecasting.baselines import (
    ClimatologyForecast,
    default_baselines,
)
from app.services.forecasting.challengers import XGBoostLagForecaster
from app.services.forecasting.ensemble import EnsembleForecaster
from app.services.forecasting.linear import LinearTrendForecaster
from app.services.forecasting.lstm import LSTMForecaster

pytestmark = pytest.mark.timeout(120)


def _series(n: int = 60) -> pd.DataFrame:
    """Deterministic, so the recorded bounds below are reproducible."""
    t = np.arange(n, dtype=float)
    y = 60 + 0.5 * t + 3 * np.sin(2 * np.pi * t / 7) + (t % 5)
    return pd.DataFrame({"ds": pd.date_range("2026-01-01", periods=n, freq="D"), "y": y})


# -- the level each producer states -------------------------------------------

def test_linear_states_95_and_keeps_ci95_for_compatibility():
    model = LinearTrendForecaster()
    model.fit(_series(), "y")
    meta = model.predict(5).metadata
    assert meta["interval_level"] == 0.95
    assert "ci95" in meta


@pytest.mark.parametrize("model", default_baselines() + [ClimatologyForecast()], ids=lambda m: m.model_name)
def test_each_baseline_states_90(model):
    model.fit(_series(1100), "y")  # climatology needs two earlier years for each day
    assert model.predict(5).metadata["interval_level"] == 0.90


def test_challenger_xgboost_lag_states_90():
    model = XGBoostLagForecaster()
    model.fit(_series(), "y")
    assert model.predict(5).metadata["interval_level"] == 0.90


def test_lstm_states_90():
    model = LSTMForecaster(seq_length=10, hidden_size=32, mc_samples=10)
    model.fit(_series(100), target_col="y")
    assert model.predict(3).metadata["interval_level"] == 0.90


def test_prophet_states_90():
    pytest.importorskip("prophet")
    from app.services.forecasting.prophet import ProphetForecaster

    model = ProphetForecaster()
    model.fit(_series(), "y")
    assert model.predict(3).metadata["interval_level"] == 0.90


def test_api_linear_fallback_states_95():
    from app.api.forecast import _linear_forecast

    response = _linear_forecast(_series(), 5, "score", [])
    assert response.metadata["interval_level"] == 0.95
    assert "ci95" in response.metadata


# -- the numeric bounds did not change ----------------------------------------
# Recorded from the code before C16, on _series_40() below. Rounded to 5-6 dp
# when written down; compared with abs=1e-4.

RECORDED_LINEAR = {
    "yhat": [82.36085, 82.87203, 83.38321, 83.89439, 84.40556],
    "lower": [77.37100, 77.88218, 78.39336, 78.90453, 79.41571],
    "upper": [87.35071, 87.86188, 88.37306, 88.88423, 89.39540],
}


def _series_40() -> pd.DataFrame:
    t = np.arange(40, dtype=float)
    y = 60 + 0.5 * t + 3 * np.sin(2 * np.pi * t / 7) + (t % 5)
    return pd.DataFrame({"ds": pd.date_range("2026-01-01", periods=40, freq="D"), "y": y})


def test_linear_bounds_are_unchanged_by_the_label():
    model = LinearTrendForecaster()
    model.fit(_series_40(), "y")
    r = model.predict(5)
    assert r.yhat == pytest.approx(RECORDED_LINEAR["yhat"], abs=1e-4)
    assert r.yhat_lower == pytest.approx(RECORDED_LINEAR["lower"], abs=1e-4)
    assert r.yhat_upper == pytest.approx(RECORDED_LINEAR["upper"], abs=1e-4)


def test_naive_bounds_are_unchanged_by_the_label():
    from app.services.forecasting.baselines import NaiveForecast

    model = NaiveForecast()
    model.fit(_series_40(), "y")
    r = model.predict(5)
    assert r.yhat == pytest.approx([82.19835] * 5, abs=1e-4)
    assert r.yhat_lower == pytest.approx(
        [77.77299, 75.93994, 74.53340, 73.34763, 72.30294], abs=1e-4)
    assert r.yhat_upper == pytest.approx(
        [86.62371, 88.45675, 89.86330, 91.04907, 92.09376], abs=1e-4)


# -- the ensemble -------------------------------------------------------------

class _Fixed:
    """A component whose forecast is fixed, so the average can be checked by hand."""

    def __init__(self, name: str, lower: float, upper: float, level):
        self.name = name
        self._lower, self._upper, self._level = lower, upper, level

    def predict(self, horizon: int) -> ForecastResult:
        return ForecastResult(
            dates=[f"2026-01-{i + 1:02d}" for i in range(horizon)],
            yhat=[(self._lower + self._upper) / 2] * horizon,
            yhat_lower=[self._lower] * horizon,
            yhat_upper=[self._upper] * horizon,
            model_name=self.name,
            model_version="1.0",
            confidence="medium",
            metadata={"interval_level": self._level},
        )


def _ensemble(active, weights):
    ens = EnsembleForecaster()
    ens._active_models = [(n, m) for n, m in active]
    ens.weights = dict(weights)
    return ens


def test_ensemble_averages_bounds_and_declares_no_common_level():
    prophet = _Fixed("Prophet", lower=60.0, upper=80.0, level=0.90)
    linear = _Fixed("LinearTrend", lower=50.0, upper=100.0, level=0.95)
    ens = _ensemble([("prophet", prophet), ("linear", linear)], {"prophet": 0.6, "linear": 0.4})

    r = ens.predict(3)

    # The bounds are the weighted average, exactly as before.
    assert r.yhat_lower == pytest.approx([0.6 * 60 + 0.4 * 50] * 3)
    assert r.yhat_upper == pytest.approx([0.6 * 80 + 0.4 * 100] * 3)
    # No single level describes a mix of 0.90 and 0.95 bounds.
    assert r.metadata["interval_level"] is None
    assert r.metadata["interval_method"] == "weighted_bounds"
    assert r.metadata["component_interval_levels"] == {"prophet": 0.90, "linear": 0.95}


def test_ensemble_lists_only_components_that_contributed():
    prophet = _Fixed("Prophet", lower=60.0, upper=80.0, level=0.90)
    linear = _Fixed("LinearTrend", lower=50.0, upper=100.0, level=0.95)
    ens = _ensemble([("prophet", prophet), ("linear", linear)], {"prophet": 1.0, "linear": 0.0})

    r = ens.predict(3)

    assert r.metadata["component_interval_levels"] == {"prophet": 0.90}
    assert r.metadata["interval_level"] is None


def test_production_stub_states_no_level():
    """The MLflow placeholder's bounds are constants, not an interval at any level."""
    from app.services.forecasting.production_forecaster import ProductionForecaster

    fc = ProductionForecaster()
    fc._fitted = True
    fc.model_source = "mlflow"
    fc.mlflow_model = object()
    r = fc.predict(3)
    assert "interval_level" in r.metadata
    assert r.metadata["interval_level"] is None


def test_forecast_result_does_not_claim_a_fixed_90_percent_ci():
    """The bounds' level is per model (metadata), so the schema must not say 90% CI."""
    import inspect

    import app.services.forecasting.base as base

    assert "90% CI" not in inspect.getsource(base)
