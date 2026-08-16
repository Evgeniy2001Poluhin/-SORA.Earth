"""Baselines must be right on series whose answer is known in advance.

A baseline exists to be the floor a real model has to clear, so a wrong one is
worse than none: it either flatters a model that beat a broken reference, or
fails a good model against an accidentally strong one. Each test below uses a
series where the correct forecast can be written down by hand.
"""
import math

import numpy as np
import pandas as pd
import pytest

from app.services.forecasting.baselines import (
    MovingAverageForecast,
    NaiveForecast,
    SeasonalNaiveForecast,
    default_baselines,
)


def series(values, start="2026-01-01", freq="D"):
    return pd.DataFrame({
        "ds": pd.date_range(start, periods=len(values), freq=freq),
        "y": values,
    })


def test_naive_carries_the_last_value_forward():
    model = NaiveForecast()
    model.fit(series([1.0, 2.0, 3.0, 7.5]), "y")
    forecast = model.predict(3)

    assert forecast.yhat == [7.5, 7.5, 7.5]
    assert forecast.dates == ["2026-01-05", "2026-01-06", "2026-01-07"]


def test_seasonal_naive_repeats_the_season_not_the_last_value():
    """The distinction the whole baseline exists for.

    On a perfectly weekly series, seasonal naive is exact and naive is wrong on
    six days out of seven. A model that cannot beat this has learned the
    calendar, not the process.
    """
    week = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0]
    history = series(week * 3)

    seasonal = SeasonalNaiveForecast(season_length=7)
    seasonal.fit(history, "y")
    naive = NaiveForecast()
    naive.fit(history, "y")

    assert seasonal.predict(7).yhat == week
    # The comparison is the point: naive repeats 70.0 and is wrong on six days
    # of seven. Asserting only that seasonal is right would pass just as well if
    # the two were the same implementation.
    assert naive.predict(7).yhat == [70.0] * 7
    wrong_days = sum(1 for a, b in zip(naive.predict(7).yhat, week) if a != b)
    assert wrong_days == 6


def test_seasonal_naive_is_exact_on_a_perfectly_periodic_series():
    week = [3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0]
    model = SeasonalNaiveForecast(season_length=7)
    model.fit(series(week * 4), "y")

    # Every seasonal error is zero, so the interval collapses -- and that is the
    # honest answer, not a bug: there is no observed variation to report.
    forecast = model.predict(7)
    assert forecast.yhat == week
    assert forecast.yhat_lower == week
    assert forecast.yhat_upper == week
    assert forecast.metadata["sigma_one_step"] == 0.0


def test_moving_average_returns_the_mean_of_the_window():
    model = MovingAverageForecast(window=3)
    model.fit(series([1.0, 2.0, 3.0, 10.0, 20.0, 30.0]), "y")
    forecast = model.predict(2)

    assert forecast.yhat == [20.0, 20.0]


def test_the_interval_widens_with_the_horizon_and_is_not_the_point_estimate():
    """`ForecastResult` promises a 90% interval. Returning the point three times
    would satisfy the type and state nothing."""
    rng = np.random.default_rng(0)
    noisy = np.cumsum(rng.normal(0, 1, 120)) + 50
    model = NaiveForecast()
    model.fit(series(noisy), "y")
    forecast = model.predict(5)

    widths = [u - l for u, l in zip(forecast.yhat_upper, forecast.yhat_lower)]
    assert widths[0] > 0, "a noisy series must not produce a zero-width interval"
    assert widths == sorted(widths), "uncertainty must not shrink with the horizon"
    # sqrt(h) growth: the fifth step is sqrt(5) times the first.
    assert widths[4] == pytest.approx(widths[0] * math.sqrt(5), rel=1e-9)


def test_gaps_are_dropped_not_filled():
    """Filling invents an observation, and the floor must not be built on one."""
    frame = series([1.0, 2.0, np.nan, 4.0, 9.0])
    model = NaiveForecast()
    model.fit(frame, "y")
    forecast = model.predict(1)

    assert forecast.yhat == [9.0]
    assert forecast.metadata["observations"] == 4


def test_a_series_too_short_for_its_season_is_refused():
    """Refusing beats returning a number estimated from nothing."""
    model = SeasonalNaiveForecast(season_length=7)
    with pytest.raises(ValueError, match="at least 8 observations"):
        model.fit(series([1.0] * 5), "y")


def test_thin_history_is_reported_as_low_confidence():
    model = NaiveForecast()
    model.fit(series([1.0, 5.0, 2.0, 8.0, 3.0]), "y")
    assert model.predict(1).confidence == "low"

    rng = np.random.default_rng(1)
    model = NaiveForecast()
    model.fit(series(np.cumsum(rng.normal(0, 1, 200))), "y")
    assert model.predict(1).confidence == "high"


def test_validate_holds_out_the_last_fifth_in_time_order():
    """Principle 12: never a random split for a series.

    On a strictly increasing series, naive must under-predict every held-out
    point. A random split would let the model see the future of its own test
    set, and the error would come out far smaller — which is the failure this
    guards.
    """
    metrics = NaiveForecast().validate(series([float(i) for i in range(100)]), "y")

    assert metrics["test_samples"] == 20.0
    # Train ends at 79, so every prediction is 79 against actuals 80..99:
    # errors 1..20, mean 10.5.
    assert metrics["mae"] == pytest.approx(10.5)
    assert metrics["rmse"] > metrics["mae"], "RMSE must exceed MAE for spread errors"


def test_mape_excludes_zeros_and_says_how_many():
    """Environmental readings do hit zero; averaging over them silently is wrong."""
    metrics = NaiveForecast().validate(series([5.0] * 80 + [0.0, 5.0] * 10), "y")
    assert metrics["mape_points_excluded"] == 10.0
    assert not math.isnan(metrics["mape"])


def test_the_default_set_includes_the_one_that_is_hard_to_beat():
    """A comparison that quietly omits seasonal naive flatters every model."""
    names = [b.model_name for b in default_baselines()]
    assert any(n.startswith("SeasonalNaive") for n in names), names
    assert any(n == "Naive" for n in names), names
    assert any(n.startswith("MovingAverage") for n in names), names


def test_predicting_before_fitting_is_refused():
    with pytest.raises(ValueError, match="not fitted"):
        NaiveForecast().predict(1)
