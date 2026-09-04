"""Tests for ensemble forecaster with auto-weighting.

Every test here fits Prophet, an LSTM and a linear model for real, so the wall
time is CPU time and scales with whatever else is running.

`test_ensemble_validate` failed on the full suite and passed on its own (#70).
Measured rather than inferred, on 8 cores:

    alone, idle machine            4.3 - 7.2 s
    inside the full suite          4.6 s      (+8% -- the suite adds nothing)
    8 busy-loops  (1x cores)      14.5 s
    24 busy-loops (3x cores)      34.7 s      <-- past pytest.ini's 30 s

So it is neither a state leak nor an order dependence: the suite context costs
8%, and contention costs 480%. It is a real timeout, and the budget was the
thing that was wrong.

It was wrong in a specific way. CI gives these tests **60 s** in the dedicated
`Run forecasting tests` step, where they run alone and fast, and **30 s** inside
`backend-tests`, where they run alongside 1300 others and are slow. The budget
was inverted with respect to the load, which is why the failure only ever
appeared in the full-suite run.

120 s, on the module, so one budget travels with the tests whichever step runs
them (a `timeout` marker outranks both the ini file and `--timeout`). That is
~17x the idle cost here and ~3.5x the worst contended measurement. It is not
tuned until it passes: a per-test timeout is there to catch a hang, and the
work these do is a bounded fit. The job-level `timeout-minutes` is still the
outer bound.
"""

import pytest
import pandas as pd
import numpy as np

from app.services.forecasting.ensemble import EnsembleForecaster

pytestmark = pytest.mark.timeout(120)


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
    """Test that LSTM is active for n=50 (above LSTM_MIN_ROWS=33 threshold)."""
    forecaster = EnsembleForecaster()

    try:
        forecaster.fit(small_timeseries, target_col="y")
    except RuntimeError:
        pytest.skip("All models failed (Prophet not installed, LSTM data too small)")

    # At least one model must have succeeded and have non-zero weight
    active_weight = max(forecaster.weights.values())
    assert active_weight > 0
    # n=50 >= LSTM_MIN_ROWS=33, so LSTM should be active with moderate weight
    assert forecaster.weights.get("lstm", 0.0) > 0.0
    assert forecaster.weights.get("prophet", 0.0) > forecaster.weights.get("lstm", 0.0)  # Prophet dominant


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


class TestBootstrapAugmentOnEmptyInput:
    """`_bootstrap_augment` on a genuinely empty frame -- the state of
    `evaluations` on a freshly deployed server, before any evaluation has
    been logged.

    `len(df) >= target_size` (the existing early-out) is False for an empty
    frame with any positive `target_size`, so execution used to reach the
    "duplicate an existing row with jitter" fallback, which assumes at least
    one row exists to duplicate: `np.random.randint(0, len(df))` with
    `len(df) == 0` raises `ValueError: high <= 0`. Measured live in
    production: this fired on every poll of `/lstm-status`, ~120 times/hour,
    always caught by a caller further up -- so nothing was outright broken,
    but the real condition (no data yet) was buried behind a numpy-internal
    message that names neither the cause nor the code that raised it.

    Kept as a direct unit test on the static method rather than going
    through `EnsembleForecaster.fit()` (as `test_ensemble_cold_start_bootstrap`
    above does): fitting Prophet/LSTM for real is what makes this test
    module need a 120s timeout, and none of that is needed to pin this one
    line's behaviour.
    """

    def test_an_empty_frame_is_returned_unchanged_not_crashed_on(self):
        empty = pd.DataFrame({"ds": pd.to_datetime([]), "y": []})
        result = EnsembleForecaster._bootstrap_augment(empty, "y", target_size=30)
        assert len(result) == 0

    def test_a_single_row_frame_still_augments_normally(self):
        """The fix must not widen the early-out beyond `df.empty` -- a
        one-row frame is not empty and must still reach the augmentation
        logic below it (which can duplicate that one row with jitter)."""
        one_row = pd.DataFrame({"ds": pd.to_datetime(["2026-01-01"]), "y": [50.0]})
        result = EnsembleForecaster._bootstrap_augment(one_row, "y", target_size=5)
        assert len(result) == 5

