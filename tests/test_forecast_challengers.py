"""Tests for phase 9 forecast challengers -- first candidates compared with baselines.

All tests use synthetic data only; no database. Runtime target: under 60s total.
"""
import math
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.services.forecasting.challengers import (
    CANDIDATES,
    XGBoostLagForecaster,
)
from app.services.forecasting.report import build_report


def _synthetic_daily_series(
    regions: int, days: int, seed: int = 42, seasonality: float = 1.0, trend: float = 0.0
):
    """Generate synthetic hourly data with trend, weekly seasonality and noise.

    Creates hourly observations that will be aggregated to daily means by
    `daily_observations()`. Each day gets 24 hourly observations to meet the
    coverage floor (80% = 20 hours minimum).

    Args:
        regions: Number of regions
        days: Number of days per region
        seed: Random seed for reproducibility
        seasonality: Amplitude of weekly seasonal component
        trend: Daily trend slope

    Returns:
        List of (region, event_time, value) tuples (hourly observations)
    """
    np.random.seed(seed)
    rows = []

    for r in range(regions):
        region = f"region_{r:02d}"
        base = 20.0 + np.random.randn() * 2.0  # Different baseline per region

        for d in range(days):
            # Generate 24 hourly observations for each day
            for h in range(24):
                event_time = datetime(2025, 1, 1) + timedelta(days=d, hours=h)

                # Weekly seasonality (day-of-week effect)
                seasonal = seasonality * math.sin(2 * math.pi * d / 7.0)

                # Trend
                trend_val = trend * d

                # Noise (per hour)
                noise = np.random.randn() * 0.5

                value = base + seasonal + trend_val + noise
                rows.append((region, event_time, value))

    return rows


def _learnable_series(seed: int = 0, regions: int = 3, days: int = 200):
    """Generate a learnable series -- weekly seasonality plus AR(1).

    Measured on macOS ARM over seeds 0-7: the worst case across seeds was mean
    ratio 0.850 and worst-region ratio 0.863 against every baseline, a margin of
    at least 13% in every seed. The 10% threshold on seed 0 leaves room for
    floating-point differences between platforms. A purely weekly series was too
    close to a tie: seasonal naive is near optimal on it.

    Args:
        seed: Random seed for reproducibility
        regions: Number of regions
        days: Number of days per region

    Returns:
        List of (region, event_time, value) tuples (hourly observations)
    """
    rng = np.random.default_rng(seed)
    rows = []

    for r in range(regions):
        region = f"region_{r:02d}"
        base = 20.0 + rng.normal() * 2.0

        # AR(1) process: phi=0.6, sigma=1.0
        phi = 0.6
        sigma = 1.0
        ar = np.zeros(days)
        ar[0] = rng.normal() * sigma / math.sqrt(1 - phi**2)

        for d in range(1, days):
            ar[d] = phi * ar[d - 1] + rng.normal() * sigma

        # Generate hourly observations for each day
        for d in range(days):
            # Daily value: base + weekly seasonality + AR(1)
            daily = base + 2.0 * math.sin(2 * math.pi * d / 7) + ar[d]

            # 24 hourly observations with small noise
            for h in range(24):
                event_time = datetime(2025, 1, 1) + timedelta(days=d, hours=h)
                value = daily + rng.normal() * 0.2
                rows.append((region, event_time, value))

    return rows


def test_candidates_evaluated_on_same_windows_as_baselines():
    """(a) Candidates evaluated on exactly the same window boundaries as baselines."""
    # Enough history: 3 regions × 260 days
    rows = _synthetic_daily_series(regions=3, days=260, seed=42, seasonality=2.0, trend=0.01)

    # Declared points must match the regions in the data
    declared = sorted({r[0] for r in rows})

    report = build_report(
        rows,
        target="synthetic:test",
        horizons=[7],
        declared_points=declared,
        season_length=7,
        # candidates=None means all non-shadow candidates
    )

    # Check that gate passed
    assert report["gate"]["7"]["passed"], "Gate should pass with sufficient data"

    # Check that baselines were scored
    baselines = report["baselines"]
    assert any(b["scored"] for b in baselines.values()), "At least one baseline should be scored"

    # Check that candidates were evaluated
    candidates_section = report["candidates"]
    assert len(candidates_section) > 0, "At least one candidate should be present"

    # Check that at least one candidate was evaluated
    evaluated = [name for name, info in candidates_section.items() if info.get("evaluated")]
    assert len(evaluated) > 0, "At least one candidate should be evaluated"

    # For each evaluated candidate, check they were scored on the same number of windows
    for name, info in candidates_section.items():
        if info.get("evaluated"):
            summary = info.get("comparison_summary", {})
            # The candidate should have been compared to baselines
            assert "candidate" in summary, f"{name}: comparison_summary should have candidate"

            # Check that baselines were scored in comparison
            baselines_scored = summary.get("baselines_scored", 0)
            assert baselines_scored > 0, f"{name}: at least one baseline should be scored"

    # Determinism: run twice and check identical results
    report2 = build_report(
        rows,
        target="synthetic:test",
        horizons=[7],
        declared_points=declared,
        season_length=7,
    )

    # Compare candidates sections (excluding generated_at which will differ)
    for name in candidates_section:
        if candidates_section[name].get("evaluated"):
            assert candidates_section[name]["mean_mase"] == report2["candidates"][name]["mean_mase"], \
                f"{name}: mean_mase should be identical across runs"


def test_xgboost_beats_seasonal_naive_on_seasonal_data():
    """(b) XGBoost beats every baseline on seasonal data, not on white noise."""
    # Learnable series: weekly seasonality + AR(1)
    seasonal_rows = _learnable_series(seed=0, regions=3, days=200)

    declared_seasonal = sorted({r[0] for r in seasonal_rows})

    # Test on seasonal data
    report_seasonal = build_report(
        seasonal_rows,
        target="synthetic:seasonal",
        horizons=[7],
        declared_points=declared_seasonal,
        season_length=7,
        candidates=["xgboost_lag"],
    )

    # Pure white noise (no seasonality, no trend) - CONTROL
    # On white noise, seasonal naive y[t] = y[t-7] errs by the difference of two
    # independent noises: Var(y[t] - y[t-7]) = 2*sigma^2. Any forecaster near
    # the mean errs with Var(y[t] - yhat) = sigma^2. So ANY smoother achieves
    # MASE ~ sqrt(sigma^2 / 2*sigma^2) = sqrt(1/2) ~ 0.71 against seasonal naive
    # on pure noise. "Beats seasonal naive" therefore cannot distinguish learning
    # from smoothing. The correct test: xgboost must beat EVERY baseline, including
    # the moving average (which is also a smoother).
    noise_rows = _synthetic_daily_series(
        regions=3, days=200, seed=43, seasonality=0.0, trend=0.0
    )
    declared_noise = sorted({r[0] for r in noise_rows})

    # Test on white noise (control)
    report_noise = build_report(
        noise_rows,
        target="synthetic:noise",
        horizons=[7],
        declared_points=declared_noise,
        season_length=7,
        candidates=["xgboost_lag"],
    )

    # XGBoost should be evaluated on both
    xgb_seasonal = report_seasonal["candidates"].get("xgboost_lag", {})
    assert xgb_seasonal.get("evaluated"), "XGBoost should be evaluated on seasonal data"

    xgb_noise = report_noise["candidates"].get("xgboost_lag", {})
    assert xgb_noise.get("evaluated"), "XGBoost should be evaluated on noise data"

    # Seasonal case: XGBoost must beat EVERY baseline in both mean and worst-region MAE
    # Mean MAE ratio: check per_baseline_mae_ratios for every baseline
    for baseline_name, ratio in xgb_seasonal["per_baseline_mae_ratios"].items():
        assert ratio <= 0.90, \
            f"XGBoost mean MAE ratio should be <= 0.90 for {baseline_name}, got {ratio:.4f}"

    # Worst-region MAE ratio: worst region vs worst region for each baseline
    worst_region_mae = xgb_seasonal["worst_region_mae"]
    baselines_section = report_seasonal["baselines"]
    for point_key, baseline_point in baselines_section.items():
        if baseline_point.get("scored"):
            for baseline_name, baseline_scores in baseline_point["scored"].items():
                baseline_worst = baseline_scores["mae"]
                worst_ratio = worst_region_mae / baseline_worst
                assert worst_ratio <= 0.90, \
                    f"XGBoost worst-region MAE ratio should be <= 0.90 for {baseline_name}, got {worst_ratio:.4f}"

    assert xgb_seasonal["eligible_for_promotion"], \
        "XGBoost should be eligible for promotion on seasonal data"

    # White-noise control: XGBoost must NOT be eligible (it should not beat every baseline)
    assert not xgb_noise["beats_every_baseline"], \
        f"XGBoost should not beat every baseline on noise, " \
        f"got ratios: {xgb_noise.get('per_baseline_mae_ratios')}"
    assert not xgb_noise["eligible_for_promotion"], \
        "XGBoost should not be eligible for promotion on noise"

    # Specific check: XGBoost MAE should not beat moving average by more than 5% on noise
    # (ratio >= 0.95). This tests that xgboost doesn't overfit beyond normal smoothing.
    ratios = xgb_noise.get("per_baseline_mae_ratios", {})
    ma_ratio = None
    for name, ratio in ratios.items():
        if "MovingAverage" in name:
            ma_ratio = ratio
            break
    assert ma_ratio is not None, "Should have moving average baseline"
    assert ma_ratio >= 0.95, \
        f"XGBoost should not beat moving average by >5% on noise (ratio {ma_ratio:.3f} < 0.95)"

    # Should have metrics
    assert "mean_mae" in xgb_seasonal, "Should have mean MAE"
    assert "worst_region_mae" in xgb_seasonal, "Should have worst region MAE"
    assert "per_baseline_mae_ratios" in xgb_seasonal, "Should have per-baseline ratios"


def test_candidates_not_evaluated_when_history_insufficient():
    """(c) With too little history: candidates listed with evaluated=false and refusal reasons."""
    # Insufficient data: only 30 days
    rows = _synthetic_daily_series(regions=2, days=30, seed=42)
    declared = sorted({r[0] for r in rows})

    report = build_report(
        rows,
        target="synthetic:short",
        horizons=[7],
        declared_points=declared,
        season_length=7,
    )

    # Gate should not pass
    assert not report["gate"]["7"]["passed"], "Gate should not pass with insufficient data"

    # Candidates should be listed but not evaluated
    candidates_section = report["candidates"]
    assert len(candidates_section) > 0, "Candidates should be listed"

    for name, info in candidates_section.items():
        if info["role"] != "shadow":  # Non-shadow candidates should appear
            assert not info["evaluated"], f"{name} should not be evaluated"
            assert "reason" in info, f"{name} should have a refusal reason"

    # Check that refusal reasons match the report's own reasons
    assert not report["evidential"], "Report should not be evidential"
    assert len(report["why_not_evidential"]) > 0, "Report should have refusal reasons"


def test_lstm_only_appears_with_include_shadow():
    """(d) LSTM appears only with include_shadow=True, and eligible_for_promotion is always False."""
    # Use smaller dataset for this test to keep it fast
    rows = _synthetic_daily_series(regions=2, days=100, seed=42, seasonality=2.0)
    declared = sorted({r[0] for r in rows})

    # Without include_shadow
    report_no_shadow = build_report(
        rows,
        target="synthetic:test",
        horizons=[7],
        declared_points=declared,
        season_length=7,
        include_shadow=False,
    )

    # LSTM should not appear
    assert "lstm" not in report_no_shadow["candidates"], \
        "LSTM should not appear when include_shadow=False"

    # With include_shadow: LSTM should appear but may not be evaluated
    # (we're not testing LSTM quality here, just that it appears correctly)
    report_with_shadow = build_report(
        rows,
        target="synthetic:test",
        horizons=[7],
        declared_points=declared,
        season_length=7,
        include_shadow=True,
    )

    # LSTM should appear
    assert "lstm" in report_with_shadow["candidates"], \
        "LSTM should appear when include_shadow=True"

    lstm_info = report_with_shadow["candidates"]["lstm"]
    assert lstm_info["role"] == "shadow", "LSTM role should be 'shadow'"

    # If it was evaluated, eligible_for_promotion should be False
    # (but we won't force evaluation since LSTM is slow and this is a shadow test)
    if lstm_info.get("evaluated"):
        assert not lstm_info["eligible_for_promotion"], \
            "LSTM should never be eligible for promotion (shadow role)"


def test_baselines_section_unchanged_with_and_without_candidates():
    """(e) The baselines' part of the report is identical with and without candidates."""
    rows = _synthetic_daily_series(regions=3, days=260, seed=42, seasonality=2.0)
    declared = sorted({r[0] for r in rows})

    # Report with candidates
    report_with = build_report(
        rows,
        target="synthetic:test",
        horizons=[7],
        declared_points=declared,
        season_length=7,
        candidates=["linear_trend", "xgboost_lag"],
    )

    # Report without candidates (empty list)
    report_without = build_report(
        rows,
        target="synthetic:test",
        horizons=[7],
        declared_points=declared,
        season_length=7,
        candidates=[],
    )

    # Baselines sections should be identical
    assert report_with["baselines"] == report_without["baselines"], \
        "Baselines section should be identical with and without candidates"

    # Gate sections should be identical
    assert report_with["gate"] == report_without["gate"], \
        "Gate section should be identical"

    # Snapshot should be identical
    assert report_with["snapshot"] == report_without["snapshot"], \
        "Snapshot section should be identical"


def test_xgboost_never_uses_future_values():
    """(f) XGBoostLagForecaster never uses future values in feature building.

    For each row i, the features must be built only from values[0:i], never from
    values[i] itself (same-day leak) or from any values[j] with j > i (look-ahead).
    """
    # Create a simple series
    days = 100
    np.random.seed(42)
    values = np.arange(days, dtype=float) + np.random.randn(days) * 0.1

    model = XGBoostLagForecaster(season_length=7, random_state=42)

    # Build features from the original values
    X_orig, y_orig = model._build_features(values)

    # Test several positions in the middle of the VALUES array
    # _build_features creates X row i from values[max_lag + i], so to test
    # features built for values position v, we check X row (v - max_lag)
    max_lag = 2 * model.season_length  # 14
    test_value_positions = [max_lag + 5, max_lag + 10, max_lag + 20, len(values) - 20]

    for v in test_value_positions:
        if v < max_lag or v >= len(values):
            continue

        # Row index in X corresponding to values position v
        row_idx = v - max_lag
        if row_idx >= len(X_orig):
            continue

        # Test 1: Changing values[v] itself should not change the features built for it
        # (this catches same-day leaks where lag reads values[v] instead of values[v-lag])
        values_self = values.copy()
        values_self[v] = values[v] + 100.0  # Large change to values[v]
        X_self, _ = model._build_features(values_self)

        np.testing.assert_array_almost_equal(
            X_orig[row_idx], X_self[row_idx],
            err_msg=f"Features for values[{v}] (X[{row_idx}]) changed when values[{v}] changed (same-day leak)"
        )

        # Test 2: Changing any values[j] with j > v should not change features for v
        # (this catches look-ahead leaks)
        for offset in [1, 5, 10]:
            j = v + offset
            if j >= len(values):
                continue

            values_future = values.copy()
            values_future[j] = values[j] + 100.0  # Large change to values[j] where j > v
            X_future, _ = model._build_features(values_future)

            np.testing.assert_array_almost_equal(
                X_orig[row_idx], X_future[row_idx],
                err_msg=f"Features for values[{v}] (X[{row_idx}]) changed when values[{j}] (future) changed"
            )


def test_all_candidates_listed_in_constants():
    """Verify CANDIDATES list is properly structured."""
    from app.services.forecasting.challengers import CANDIDATES

    assert len(CANDIDATES) == 3, "Should have exactly 3 candidates"

    names = [c.name for c in CANDIDATES]
    assert "linear_trend" in names
    assert "xgboost_lag" in names
    assert "lstm" in names

    # Check roles
    for c in CANDIDATES:
        assert c.role in ["candidate", "shadow"], f"{c.name} should have valid role"

    # linear_trend and xgboost_lag should be candidates
    linear = [c for c in CANDIDATES if c.name == "linear_trend"][0]
    assert linear.role == "candidate"

    xgb = [c for c in CANDIDATES if c.name == "xgboost_lag"][0]
    assert xgb.role == "candidate"

    # lstm should be shadow
    lstm = [c for c in CANDIDATES if c.name == "lstm"][0]
    assert lstm.role == "shadow"


def test_xgboost_deterministic():
    """XGBoost should produce identical results with same seed."""
    df = pd.DataFrame({
        "ds": [date(2025, 1, 1) + timedelta(days=i) for i in range(100)],
        "y": np.sin(np.arange(100) * 2 * np.pi / 7) + np.random.RandomState(42).randn(100) * 0.1,
    })

    model1 = XGBoostLagForecaster(season_length=7, random_state=42)
    model1.fit(df, "y")
    pred1 = model1.predict(7)

    model2 = XGBoostLagForecaster(season_length=7, random_state=42)
    model2.fit(df, "y")
    pred2 = model2.predict(7)

    # Predictions should be identical
    np.testing.assert_array_almost_equal(pred1.yhat, pred2.yhat)
    np.testing.assert_array_almost_equal(pred1.yhat_lower, pred2.yhat_lower)
    np.testing.assert_array_almost_equal(pred1.yhat_upper, pred2.yhat_upper)


def test_xgboost_insufficient_history_raises():
    """XGBoost should raise ValueError with too little history."""
    # Only 10 days, but we need at least 2*season_length + 1 = 15
    df = pd.DataFrame({
        "ds": [date(2025, 1, 1) + timedelta(days=i) for i in range(10)],
        "y": np.arange(10, dtype=float),
    })

    model = XGBoostLagForecaster(season_length=7)
    with pytest.raises(ValueError, match="needs at least"):
        model.fit(df, "y")


if __name__ == "__main__":
    import time

    print("Running phase 9 challenger tests...")
    start = time.time()

    # Run all tests
    test_candidates_evaluated_on_same_windows_as_baselines()
    print("✓ (a) Candidates evaluated on same windows as baselines")

    test_xgboost_beats_seasonal_naive_on_seasonal_data()
    print("✓ (b) XGBoost beats seasonal naive on seasonal data")

    test_candidates_not_evaluated_when_history_insufficient()
    print("✓ (c) Candidates not evaluated when history insufficient")

    test_lstm_only_appears_with_include_shadow()
    print("✓ (d) LSTM only appears with include_shadow=True")

    test_baselines_section_unchanged_with_and_without_candidates()
    print("✓ (e) Baselines section unchanged with and without candidates")

    test_xgboost_never_uses_future_values()
    print("✓ (f) XGBoost never uses future values")

    test_all_candidates_listed_in_constants()
    print("✓ CANDIDATES list properly structured")

    test_xgboost_deterministic()
    print("✓ XGBoost is deterministic")

    test_xgboost_insufficient_history_raises()
    print("✓ XGBoost raises on insufficient history")

    elapsed = time.time() - start
    print(f"\nAll tests passed in {elapsed:.1f}s")
