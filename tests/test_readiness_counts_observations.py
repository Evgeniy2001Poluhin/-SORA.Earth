"""Readiness must count observations, not the calendar days between them.

`load_time_series` reindexes onto every day between the first and last reading
and fills the interior by interpolation, so the length of what it returns is a
span. `EnsembleForecaster.fit` read exactly that number and chose its weighting
tier from it.

On the production series that was 14 observations across a 51-day span: the
ensemble saw n=51, landed in the 50..100 tier and switched LSTM on at weight
0.4 -- a model the same data would not reach on its own count, since
`LSTM_MIN_ROWS` is 33. The interpolation was doing the promoting, and nothing
in the pipeline could tell "we have enough data" from "we have a wide enough
date range" (#132).

The guard the arrangement had no way to fail: a sparse series whose observation
count is below the threshold must not reach the LSTM tier because its span is
above it.
"""
import pandas as pd
import pytest

from app.services.forecasting.ensemble import LSTM_MIN_ROWS


def _sparse_but_wide(observations, span_days):
    """Few readings, far apart -- exactly the shape that was being promoted.

    Built the way the loader builds one: a value on each observed day, the
    interior filled by interpolation, and a flag saying which was which.
    """
    start = pd.Timestamp("2026-01-01")
    dates = pd.date_range(start, periods=span_days, freq="D")
    observed_at = {dates[round(i * (span_days - 1) / (observations - 1))]
                   for i in range(observations)}

    frame = pd.DataFrame({"ds": dates})
    frame["y"] = [float(10 + i) if d in observed_at else None
                  for i, d in enumerate(dates)]
    frame["y"] = frame["y"].interpolate(method="linear")
    frame["is_observed"] = frame["ds"].isin(observed_at)
    return frame


def _lstm_weight_after_fit(frame, monkeypatch):
    """The weight the ensemble itself assigns, with the models stubbed out.

    Every readiness assertion goes through this. Computing the count in the
    test instead would assert the test's own arithmetic: a regression inside
    `EnsembleForecaster.fit` would leave it green.
    """
    from app.services.forecasting import ensemble as ens

    forecaster = ens.EnsembleForecaster()
    for name in ("lstm", "prophet", "linear"):
        monkeypatch.setattr(getattr(forecaster, name), "fit", lambda *a, **k: None)
    forecaster.fit(frame, target_col="y")
    return forecaster.weights["lstm"]


def test_the_fixture_is_the_shape_under_test():
    """Otherwise the assertions below could pass on a series that is not sparse."""
    frame = _sparse_but_wide(observations=14, span_days=51)

    assert len(frame) == 51
    assert int(frame["is_observed"].sum()) == 14
    assert 14 < LSTM_MIN_ROWS < 51, (
        f"the thresholds moved: LSTM_MIN_ROWS is {LSTM_MIN_ROWS}, and this test "
        f"needs an observation count below it and a span above it to mean "
        f"anything"
    )


def test_a_wide_span_does_not_promote_a_sparse_series(monkeypatch):
    """The defect in one assertion, read off the ensemble's own decision."""
    weight = _lstm_weight_after_fit(
        _sparse_but_wide(observations=14, span_days=51), monkeypatch)

    assert weight == 0.0, (
        f"LSTM was given weight {weight} on a series of 14 observations; only "
        f"the 51-day span could have put it there, and LSTM_MIN_ROWS is "
        f"{LSTM_MIN_ROWS}"
    )


def test_a_frame_without_provenance_still_counts_rows(monkeypatch):
    """Anything that never went through the loader behaves exactly as before.

    Asserted through the ensemble rather than by recomputing the fallback here:
    an earlier draft did the latter and would have stayed green through any
    regression in `fit`.
    """
    plain = pd.DataFrame({
        "ds": pd.date_range("2026-01-01", periods=40, freq="D"),
        "y": [float(i) for i in range(40)],
    })
    assert "is_observed" not in plain.columns

    assert _lstm_weight_after_fit(plain, monkeypatch) > 0.0, (
        "40 rows without provenance are 40 samples, above LSTM_MIN_ROWS; "
        "counting them differently would starve callers that never used the "
        "loader"
    )


def test_a_dense_series_is_not_held_back(monkeypatch):
    """The other direction: counting observations must not starve real data.

    Without this, a count wrong in the conservative direction would look like a
    fix -- everything sparse refused, and nothing to say it went too far.
    """
    dense = _sparse_but_wide(observations=40, span_days=40)
    assert int(dense["is_observed"].sum()) == 40

    assert _lstm_weight_after_fit(dense, monkeypatch) > 0.0
