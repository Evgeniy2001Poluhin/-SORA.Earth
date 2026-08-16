"""Does the model beat a simple baseline? Nothing could answer that (#199 phase 6).

Engineering principle 14 requires it. `baselines.py` supplied the opponents and
`evaluate_rolling_origin` the arena, and nothing joined them -- so "beats a
baseline" was a rule with no way to apply it.

The cases below use series whose winner is known in advance, and the ones that
matter are the refusals: a candidate that cannot be scored must never read as a
win, and a comparison whose opponent set quietly shrank is a different claim
from the one it appears to make.
"""
import math

import numpy as np
import pandas as pd
import pytest

from app.services.forecasting.baselines import (
    NaiveForecast,
    SeasonalNaiveForecast,
    default_baselines,
)
from app.services.forecasting.evaluation import compare_to_baselines


def series(values, start="2026-01-01"):
    return pd.DataFrame({
        "ds": pd.date_range(start, periods=len(values), freq="D"),
        "y": np.asarray(values, dtype=float),
    })


WEEK = [10.0, 12.0, 9.0, 14.0, 11.0, 13.0, 10.5]


def weekly(cycles=14, noise=0.2, seed=7):
    rng = np.random.default_rng(seed)
    return np.tile(WEEK, cycles) + rng.normal(0, noise, 7 * cycles)


def test_a_model_that_knows_the_future_beats_every_baseline():
    values = weekly()
    data = series(values)

    def oracle(train_df, horizon):
        start = len(train_df)
        return list(values[start:start + horizon])

    result = compare_to_baselines(oracle, data, "y", horizon=7, train_size=42,
                                  n_windows=5, candidate_name="oracle")

    assert result.verdict == "beats_all"
    assert result.losses() == []
    assert result.summary()["baselines_scored"] == 3


def test_a_model_that_is_only_seasonal_naive_does_not_beat_it():
    """Ties are losses. A model that matches the floor has not cleared it."""
    data = series(weekly())

    def imitator(train_df, horizon):
        history = train_df["y"].to_numpy(dtype=float)[-7:]
        return [history[i % 7] for i in range(horizon)]

    result = compare_to_baselines(imitator, data, "y", horizon=7, train_size=42,
                                  n_windows=5, candidate_name="imitator")

    assert result.verdict == "loses_to_some"
    assert any(name.startswith("SeasonalNaive") for name in result.losses()), result.losses()


def test_the_hardest_baseline_is_named_so_nobody_reruns_to_find_it():
    """On a weekly series it is seasonal naive, and that must be visible."""
    data = series(weekly())

    def flat(train_df, horizon):
        return [float(train_df["y"].mean())] * horizon

    result = compare_to_baselines(flat, data, "y", horizon=7, train_size=42,
                                  n_windows=5)

    name, value = result.hardest()
    assert name.startswith("SeasonalNaive"), name
    assert value == pytest.approx(result.summary()["hardest_baseline_score"])


def test_every_contestant_is_scored_on_the_same_windows():
    """Comparing across different splits compares arrangements, not models."""
    data = series(weekly())

    def flat(train_df, horizon):
        return [11.0] * horizon

    result = compare_to_baselines(flat, data, "y", horizon=7, train_size=42,
                                  n_windows=4)

    spans = {
        name: [(w.train_end, w.test_start, w.test_end) for w in report.windows]
        for name, report in result.baselines.items()
    }
    spans["candidate"] = [(w.train_end, w.test_start, w.test_end)
                          for w in result.candidate.windows]
    reference = spans["candidate"]
    for name, windows in spans.items():
        assert windows == reference, f"{name} was scored on different windows"


def test_a_baseline_that_cannot_be_scored_is_named_not_dropped():
    """The ones that fall out need the most history — seasonal naive first.

    A verdict of `beats_all` over a set that silently shrank to two is a
    different claim from the same words over three.
    """
    data = series(weekly(cycles=8))

    def flat(train_df, horizon):
        return [11.0] * horizon

    # 45 needs 46 observations; the training slice is 42, so it cannot be
    # scored. 40 would have fitted -- the first version of this test used it
    # and passed for the wrong reason.
    long_season = SeasonalNaiveForecast(season_length=45)
    result = compare_to_baselines(
        flat, data, "y", horizon=7, train_size=42, n_windows=2,
        baselines=[NaiveForecast(), long_season],
    )

    assert long_season.model_name in result.skipped, result.skipped
    summary = result.summary()
    assert summary["baselines_skipped"], "the shrunken opponent set was not reported"
    assert summary["baselines_scored"] == 1


def test_a_candidate_that_could_not_be_scored_is_not_a_win():
    """`not_comparable` exists so an unscored model never reads as beating anything."""
    data = series(weekly())

    def wrong_length(train_df, horizon):
        return [1.0] * (horizon - 1)

    with pytest.raises(ValueError, match="expected 7 forecasts"):
        compare_to_baselines(wrong_length, data, "y", horizon=7, train_size=42,
                             n_windows=2)


def test_with_no_baselines_at_all_the_verdict_is_not_a_win():
    data = series(weekly())

    def flat(train_df, horizon):
        return [11.0] * horizon

    result = compare_to_baselines(flat, data, "y", horizon=7, train_size=42,
                                  n_windows=2, baselines=[])

    assert result.verdict == "not_comparable", "beating nothing is not beating everything"
    assert result.hardest() == (None, pytest.approx(float("nan"), nan_ok=True))


def test_the_summary_carries_the_worst_window_and_an_interval():
    """A mean alone is how 0.81 on 171 rows came to read as clearing 0.80."""
    data = series(weekly())

    def flat(train_df, horizon):
        return [11.0] * horizon

    summary = compare_to_baselines(flat, data, "y", horizon=7, train_size=42,
                                   n_windows=5).summary()

    assert summary["candidate_worst_window"] >= summary["candidate"]
    assert summary["candidate_ci"]["n_windows"] == 5.0
    assert not math.isnan(summary["candidate_ci"]["low"])


def test_the_default_opponent_set_is_the_full_ladder():
    names = [b.model_name for b in default_baselines()]
    assert len(names) == 3, names
