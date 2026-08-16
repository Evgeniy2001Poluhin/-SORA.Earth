"""The harness must be right before any number it produces means anything.

Every case below uses a series whose correct answer can be written down by hand,
because a scoring harness that is quietly wrong is worse than none: it produces
numbers that look like evidence and are not.
"""
import numpy as np
import pandas as pd
import pytest

from app.services.forecasting.evaluation import (
    EvaluationReport,
    _seasonal_scale,
    evaluate_rolling_origin,
    rolling_origin_windows,
)


def series(values, start="2026-01-01"):
    return pd.DataFrame({
        "ds": pd.date_range(start, periods=len(values), freq="D"),
        "y": np.asarray(values, dtype=float),
    })


def test_windows_never_test_on_data_they_trained_on():
    """The one property that makes the whole thing a forecast and not a fit."""
    windows = rolling_origin_windows(n_observations=100, train_size=30, horizon=7, n_windows=5)

    assert len(windows) == 5
    for w in windows:
        assert w["test_start"] >= w["train_end"], "a test point precedes the training cut"
        assert w["test_end"] - w["test_start"] == 7
    # Default step is the horizon, so no test point is scored twice.
    spans = [range(w["test_start"], w["test_end"]) for w in windows]
    flat = [i for span in spans for i in span]
    assert len(flat) == len(set(flat)), "test periods overlap; points scored more than once"


def test_asking_for_more_windows_than_the_data_allows_is_refused():
    """A report over eight windows labelled twelve is the shortfall §7 exists to stop."""
    # 30 training + 7 horizon + 7 step x 10 further origins = 107.
    with pytest.raises(ValueError, match="need 107 observations, have 50"):
        rolling_origin_windows(n_observations=50, train_size=30, horizon=7, n_windows=11)


def test_mase_below_one_means_the_model_beat_seasonal_naive():
    """The number principle 14 is about.

    A perfect forecaster on a noisy series must score MASE well under 1; a
    forecaster that is exactly seasonal naive must score about 1.
    """
    rng = np.random.default_rng(7)
    week = np.array([10.0, 12.0, 9.0, 14.0, 11.0, 13.0, 10.5])
    values = np.tile(week, 12) + rng.normal(0, 0.2, 84)
    data = series(values)

    def oracle(train_df, horizon):
        start = len(train_df)
        return list(values[start:start + horizon])

    def seasonal_naive(train_df, horizon):
        history = train_df["y"].to_numpy(dtype=float)[-7:]
        return [history[i % 7] for i in range(horizon)]

    perfect = evaluate_rolling_origin(oracle, data, "y", horizon=7, train_size=28,
                                      n_windows=5, model_name="oracle")
    matched = evaluate_rolling_origin(seasonal_naive, data, "y", horizon=7, train_size=28,
                                      n_windows=5, model_name="seasonal_naive")

    assert perfect.mean("mase") < 0.1
    assert matched.mean("mase") == pytest.approx(1.0, abs=0.35)
    assert perfect.beats(matched, "mase")


def test_mase_does_not_move_when_only_the_test_period_gets_volatile():
    """The classic MASE bug: scaling by the test set instead of the training set.

    Two datasets share a training slice and differ only after the cut. A
    denominator taken from the test period would change; a correct one cannot.
    """
    rng = np.random.default_rng(3)
    # Textured, not perfectly periodic: a flat seasonal difference would make the
    # scale zero, which the module refuses outright (see the test below).
    train_part = list(np.tile([5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0], 6)
                      + rng.normal(0, 0.7, 42))
    # Both forecasts are wrong, so neither MAE is zero and the ratio is defined.
    calm = train_part + [9.0] * 7
    wild = train_part + list(9.0 + rng.normal(0, 50, 7))

    def flat(train_df, horizon):
        return [8.0] * horizon

    calm_report = evaluate_rolling_origin(flat, series(calm), "y", horizon=7,
                                          train_size=42, n_windows=1)
    wild_report = evaluate_rolling_origin(flat, series(wild), "y", horizon=7,
                                          train_size=42, n_windows=1)

    expected = _seasonal_scale(np.asarray(train_part, dtype=float), 7)
    calm_scale = calm_report.windows[0].mae / calm_report.windows[0].mase
    wild_scale = wild_report.windows[0].mae / wild_report.windows[0].mase

    assert calm_scale == pytest.approx(expected), "calm window did not scale by the training slice"
    assert wild_scale == pytest.approx(expected), (
        "the MASE denominator moved with the test period, so it was computed there"
    )
    assert wild_report.windows[0].mae > calm_report.windows[0].mae, (
        "the volatile period must actually be harder, or the test proves nothing"
    )


def test_bias_separates_being_wrong_from_being_low():
    """Same MAE, different problem. An unsigned metric cannot tell them apart."""
    values = [10.0] * 40
    balanced = list(values) + [12.0, 8.0, 12.0, 8.0, 12.0, 8.0, 12.0]
    low = list(values) + [8.0] * 7
    # A perfectly constant training slice has a zero seasonal scale, so give it
    # texture the scale can be computed from.
    rng = np.random.default_rng(11)
    texture = list(rng.normal(10.0, 1.0, 40))

    def flat(train_df, horizon):
        return [10.0] * horizon

    a = evaluate_rolling_origin(flat, series(texture + balanced[40:]), "y",
                                horizon=7, train_size=40, n_windows=1)
    b = evaluate_rolling_origin(flat, series(texture + low[40:]), "y",
                                horizon=7, train_size=40, n_windows=1)

    assert a.windows[0].mae == pytest.approx(b.windows[0].mae, rel=0.35)
    assert abs(a.windows[0].bias) < 1.0, "alternating errors should nearly cancel"
    assert b.windows[0].bias == pytest.approx(-2.0), "a consistently high forecast must show"


def test_interval_coverage_counts_what_actually_landed_inside():
    rng = np.random.default_rng(5)
    data = series(rng.normal(10.0, 1.0, 60))

    def flat(train_df, horizon):
        return [10.0] * horizon

    def always_wide(train_df, horizon):
        return [[-1e6, 1e6]] * horizon

    def always_narrow(train_df, horizon):
        return [[10.0, 10.0]] * horizon

    wide = evaluate_rolling_origin(flat, data, "y", horizon=7, train_size=40,
                                   n_windows=2, interval=always_wide)
    narrow = evaluate_rolling_origin(flat, data, "y", horizon=7, train_size=40,
                                     n_windows=2, interval=always_narrow)

    assert wide.mean("interval_coverage") == 1.0
    assert narrow.mean("interval_coverage") == 0.0


def test_the_worst_window_is_reported_beside_the_mean():
    """Excellent in ten windows and catastrophic in two is not a model to promote."""
    report = EvaluationReport(model_name="m", horizon=7)
    from app.services.forecasting.evaluation import WindowResult
    for i, mae in enumerate([1.0, 1.0, 1.0, 40.0]):
        report.windows.append(WindowResult(i, 0, 0, 0, mae, mae, mae, 0.0, None))

    assert report.mean("mae") == pytest.approx(10.75)
    assert report.worst("mae") == 40.0


def test_a_confidence_interval_is_reported_over_windows():
    """Twelve numbers are a small sample; a bare mean invites 0.81-clears-0.80."""
    from app.services.forecasting.evaluation import WindowResult
    report = EvaluationReport(model_name="m", horizon=7)
    rng = np.random.default_rng(2)
    for i, mae in enumerate(rng.normal(10.0, 3.0, 12)):
        report.windows.append(WindowResult(i, 0, 0, 0, float(mae), 0.0, 0.0, 0.0, None))

    ci = report.confidence_interval("mae")
    assert ci["n_windows"] == 12.0
    assert ci["low"] < report.mean("mae") < ci["high"]


def test_a_perfectly_periodic_training_slice_is_refused_not_divided_by():
    """Zero denominator would produce inf and poison every average downstream."""
    values = list(np.tile([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0], 8))

    def flat(train_df, horizon):
        return [4.0] * horizon

    with pytest.raises(ValueError, match="perfectly periodic"):
        evaluate_rolling_origin(flat, series(values), "y", horizon=7,
                                train_size=42, n_windows=1)


def test_a_model_returning_the_wrong_number_of_points_is_refused():
    rng = np.random.default_rng(9)

    def short(train_df, horizon):
        return [1.0] * (horizon - 1)

    with pytest.raises(ValueError, match="expected 7 forecasts, got 6"):
        evaluate_rolling_origin(short, series(rng.normal(10, 1, 60)), "y",
                                horizon=7, train_size=40, n_windows=1)


def test_the_callable_only_ever_sees_its_own_training_rows():
    """If it saw more, every score after it would be a fit, not a forecast."""
    rng = np.random.default_rng(4)
    data = series(rng.normal(10, 1, 80))
    seen = []

    def recording(train_df, horizon):
        seen.append((len(train_df), train_df["ds"].max()))
        return [float(train_df["y"].iloc[-1])] * horizon

    evaluate_rolling_origin(recording, data, "y", horizon=7, train_size=40, n_windows=3)

    assert [n for n, _ in seen] == [40, 47, 54]
    for length, last_seen in seen:
        assert last_seen == data["ds"].iloc[length - 1]
