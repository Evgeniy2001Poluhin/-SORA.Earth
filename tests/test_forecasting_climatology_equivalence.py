"""The indexed ClimatologyForecast gives exactly the forecasts of the original scan.

The first version compared every forecast day, year and offset against every
date in the series, so one fit inside the rolling-origin evaluation took minutes.
The rewrite builds a date index once per fit. It must not change a single
number, so the original class is kept below, verbatim, as the reference these
tests compare against. It exists only in this file: nothing in app/ imports it.
"""
from __future__ import annotations

from datetime import date as dt_date, timedelta
from typing import Dict, Optional

import numpy as np
import pandas as pd
import pytest

from app.services.forecasting.baselines import (
    ClimatologyForecast,
    _Baseline,
    _infer_step,
    _prepare,
)

#: Origins chosen for the calendar they sit on, not for their values.
ORIGINS = [
    dt_date(2023, 12, 30),  # near New Year: targets cross into the next year
    dt_date(2024, 1, 2),    # just after New Year: windows reach into January
    dt_date(2024, 2, 28),   # the day before 29 February in a leap year
    dt_date(2024, 2, 29),   # 29 February is the origin itself
    dt_date(2024, 3, 1),    # just after the leap day
]


def _gapped_five_year_series(seed: int) -> pd.DataFrame:
    """Daily values for 2020-2024 with about a tenth of the dates missing.

    One observation is repeated with a different value. The original scan keeps
    both, so the index must keep both, in the same order.
    """
    rng = np.random.default_rng(seed)
    days = pd.date_range("2020-01-01", "2024-12-31", freq="D")
    seasonal = 15.0 + 10.0 * np.sin(2.0 * np.pi * days.dayofyear.to_numpy() / 365.25)
    values = seasonal + rng.normal(0.0, 2.0, len(days))
    keep = rng.random(len(days)) >= 0.10
    frame = pd.DataFrame({"ds": days[keep], "y": values[keep]})
    repeated = frame.iloc[[len(frame) // 3]].assign(y=lambda f: f["y"] + 5.0)
    return pd.concat([frame, repeated], ignore_index=True)


# ---------------------------------------------------------------------------
# Reference: ClimatologyForecast verbatim, as it stood before the indexed
# rewrite (app/services/forecasting/baselines.py at commit 06cc1c6f).
# Do not edit this class to match the implementation. It is the oracle.
# ---------------------------------------------------------------------------

class _ReferenceClimatologyForecast(_Baseline):
    """Mean of the same calendar week in previous years.

    The forecast for day *d* is the mean of the target over every calendar day
    within ±3 days of *d*'s month-day, in every earlier year whose matching days
    all fall on or before the origin (the last training day).

    This is the climatological baseline: for a temperature target, a model that
    cannot beat the average of the same week in previous years has learned the
    calendar, not the weather.

    29 February is matched as 28 February in non-leap years. At least two earlier
    years are required for each forecast point; where there are fewer, that
    fit/predict raises ValueError to signal the point is not scorable.

    Added for M3-A (docs/M3A_ARCHIVE_DECLARATION.md §7), where nine years of
    history make it both possible and obligatory. NOT included in default_baselines,
    so M3 v1's report is unchanged.
    """

    def __init__(self, window_days: int = 3):
        """
        Args:
            window_days: how many days on each side of the target day to include
                        (±3 gives a 7-day window)
        """
        if window_days < 0:
            raise ValueError(f"window_days must be >= 0, got {window_days}")
        super().__init__(f"Climatology(±{window_days}d)", "1.0")
        self.window_days = window_days
        self._dates: Optional[pd.Series] = None

    def _min_observations(self) -> int:
        # Need enough history to potentially have 2 earlier years
        # The actual 2-year check happens at predict time
        return 365

    def fit(self, df: pd.DataFrame, target_col: str, **kwargs) -> None:
        """Fit stores the dates and values for lookup during prediction."""
        values = _prepare(df, target_col)
        needed = self._min_observations()
        if len(values) < needed:
            raise ValueError(
                f"{self.model_name} needs at least {needed} observations, got {len(values)}"
            )

        # Store dates alongside values for climatology matching
        frame = df[["ds", target_col]].copy()
        frame["ds"] = pd.to_datetime(frame["ds"])
        frame = frame.dropna(subset=[target_col]).sort_values("ds")
        self._dates = frame["ds"].reset_index(drop=True)
        self._values = values
        self._last_date = self._dates.iloc[-1]
        self._step = _infer_step(df)

        # For interval estimation, compute one-step errors
        predicted = self._one_step_predictions(values)
        errors = values.to_numpy(dtype=float) - predicted
        errors = errors[~np.isnan(errors)]
        self._n_errors = int(errors.size)
        self._sigma = float(np.std(errors, ddof=1)) if errors.size > 1 else 0.0

    def _point_forecast(self, values: pd.Series, horizon: int) -> np.ndarray:
        """Forecast each future day by averaging matching days from earlier years.

        For each forecast day d, the window is the 7 actual dates anchor−3 … anchor+3
        where anchor is d's month-day placed in an earlier year Y (with 29 Feb → 28 Feb
        anchor). This crosses year boundaries naturally: forecasting 1 Jan uses
        29 Dec Y−1 … 4 Jan Y.

        A year Y qualifies if it is earlier than d's year AND all 7 window dates ≤ origin.
        """
        if self._dates is None:
            raise ValueError("ClimatologyForecast not fitted")

        origin = self._last_date.date()
        forecasts = []
        dates_array = self._dates.dt.date.to_numpy()
        values_array = values.to_numpy(dtype=float)

        for h in range(horizon):
            forecast_date = (self._last_date + self._step * (h + 1)).date()

            earlier_years_values = []
            years_contributed = set()

            all_years = sorted(set(d.year for d in dates_array))
            for year in all_years:
                # Year must be earlier than the target's year
                if year >= forecast_date.year:
                    continue

                # Anchor = forecast's month-day in this year, with 29 Feb → 28 Feb
                anchor_month, anchor_day = forecast_date.month, forecast_date.day
                if anchor_month == 2 and anchor_day == 29:
                    anchor_month, anchor_day = 2, 28

                try:
                    anchor = dt_date(year, anchor_month, anchor_day)
                except ValueError:
                    continue

                # Build the 7-day window: anchor−3 … anchor+3 (crosses year boundaries)
                window_dates = [anchor + timedelta(days=offset)
                                for offset in range(-self.window_days, self.window_days + 1)]

                # All 7 dates must be <= origin for this year to qualify
                if any(d > origin for d in window_dates):
                    continue

                # Collect observed values from this year's window
                year_values = []
                for window_date in window_dates:
                    mask = dates_array == window_date
                    if mask.any():
                        year_values.extend(values_array[mask].tolist())

                if year_values:
                    earlier_years_values.extend(year_values)
                    years_contributed.add(year)

            if len(years_contributed) < 2:
                raise ValueError(
                    f"Climatology: only {len(years_contributed)} earlier year(s) found for "
                    f"{forecast_date.isoformat()}, need at least 2"
                )

            if not earlier_years_values:
                raise ValueError(
                    f"Climatology: no values collected for {forecast_date.isoformat()}"
                )

            forecasts.append(float(np.mean(earlier_years_values)))

        return np.array(forecasts)

    def _one_step_predictions(self, values: pd.Series) -> np.ndarray:
        """One-step-ahead climatology predictions for interval estimation.

        For each point i, predict using climatology from data before i.
        """
        if self._dates is None:
            raise ValueError("ClimatologyForecast not fitted")

        array = values.to_numpy(dtype=float)
        dates_array = self._dates.dt.date.to_numpy()
        predicted = np.full_like(array, np.nan)

        min_idx = 365
        if len(array) < min_idx:
            return predicted

        for i in range(min_idx, len(array)):
            target_date = dates_array[i]
            origin = dates_array[i - 1]

            earlier_values = []
            years_seen = set()

            all_years = sorted(set(d.year for d in dates_array[:i]))
            for year in all_years:
                if year >= target_date.year:
                    continue

                anchor_month, anchor_day = target_date.month, target_date.day
                if anchor_month == 2 and anchor_day == 29:
                    anchor_month, anchor_day = 2, 28

                try:
                    anchor = dt_date(year, anchor_month, anchor_day)
                except ValueError:
                    continue

                window_dates = [anchor + timedelta(days=offset)
                                for offset in range(-self.window_days, self.window_days + 1)]

                if any(d > origin for d in window_dates):
                    continue

                for window_date in window_dates:
                    mask = (dates_array[:i] == window_date)
                    if mask.any():
                        earlier_values.extend(array[:i][mask].tolist())
                        years_seen.add(year)

            if len(years_seen) >= 2 and earlier_values:
                predicted[i] = float(np.mean(earlier_values))

        return predicted

    def _reconstruct_kwargs(self) -> Dict:
        return {"window_days": self.window_days}


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("origin", ORIGINS, ids=lambda d: d.isoformat())
def test_indexed_climatology_gives_the_original_scans_forecasts(origin):
    """Fitted on the same history, both classes agree on the in-sample one-step
    predictions and on every forecast, exactly, for horizons 1, 7 and 30."""
    frame = _gapped_five_year_series(seed=20261011)
    train = frame[frame["ds"] <= pd.Timestamp(origin)]

    reference = _ReferenceClimatologyForecast(window_days=3)
    reference.fit(train, "y")
    current = ClimatologyForecast(window_days=3)
    current.fit(train, "y")

    np.testing.assert_array_equal(
        current._one_step_predictions(current._values),
        reference._one_step_predictions(reference._values),
    )
    assert current._sigma == reference._sigma
    assert current._n_errors == reference._n_errors

    for horizon in (1, 7, 30):
        expected = reference.predict(horizon)
        actual = current.predict(horizon)
        assert actual.dates == expected.dates
        np.testing.assert_array_equal(actual.yhat, expected.yhat)
        np.testing.assert_array_equal(actual.yhat_lower, expected.yhat_lower)
        np.testing.assert_array_equal(actual.yhat_upper, expected.yhat_upper)
