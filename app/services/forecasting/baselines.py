"""The simple forecasts every model has to beat before it means anything (#199 phase 6).

Engineering principle 14 says every model must beat a simple baseline. Measured
2026-08-16: this package held `linear`, `prophet`, `lstm` and `ensemble`, and no
baseline at all -- so the principle could not be applied, because there was
nothing to lose to. A model that beats nothing has not been shown to be worth
its complexity, and "MAE 18.2" alone says nothing about whether carrying last
week's value forward would have done better.

Three baselines, in the order they get harder to beat:

- **Naive** -- carry the last observation forward. The reference for any series
  with a trend and no seasonality.
- **Seasonal naive** -- carry the value from one season ago. For hourly or daily
  environmental data this is the one that hurts: air quality and temperature
  repeat weekly and daily, and a model that cannot beat it has learned the
  calendar rather than the process.
- **Moving average** -- the mean of the last *k* observations. Beats naive on
  noisy series and loses on trending ones, which is why both are kept.

## The intervals are not decoration

`ForecastResult` requires `yhat_lower`/`yhat_upper`, and it would be easy to
return the point estimate three times. These use the textbook random-walk
interval: the standard deviation of the method's own one-step in-sample errors,
widened by sqrt(h) because errors accumulate over the horizon. That is a real
statement -- it can be checked against realised coverage, and it is the number
`interval_coverage` in the evaluation harness will be scored against.

`confidence` reports how much history the interval was estimated from, not how
good the forecast is. A baseline makes no claim to be good.
"""
import math
from datetime import date as dt_date, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.services.forecasting.base import BaseForecastModel, ForecastResult

#: 1.645 sigma is the 90% two-sided normal interval; the level is reported as
#: metadata["interval_level"] = 0.90.
Z_90 = 1.6448536269514722

#: Below this many one-step errors the spread is estimated from too little to be
#: worth reporting as anything but low confidence.
_THIN_HISTORY = 30


def _prepare(df: pd.DataFrame, target_col: str) -> pd.Series:
    """The observations, in time order, with gaps dropped rather than filled.

    Filling a gap invents an observation, and a baseline whose job is to be an
    honest floor must not do that. A dropped day is one fewer error to estimate
    the interval from, which is visible in `confidence`.
    """
    if "ds" not in df.columns:
        raise ValueError("df must carry a 'ds' date column")
    if target_col not in df.columns:
        raise ValueError(f"df has no column {target_col!r}")

    frame = df[["ds", target_col]].copy()
    frame["ds"] = pd.to_datetime(frame["ds"])
    frame = frame.dropna(subset=[target_col]).sort_values("ds")

    values = pd.to_numeric(frame[target_col], errors="coerce").dropna()
    if values.empty:
        raise ValueError(f"no usable observations in {target_col!r}")
    return values.reset_index(drop=True)


def _future_dates(last_date: pd.Timestamp, horizon: int, step: pd.Timedelta) -> List[str]:
    return [(last_date + step * (i + 1)).strftime("%Y-%m-%d") for i in range(horizon)]


def _infer_step(df: pd.DataFrame) -> pd.Timedelta:
    """The spacing the series actually has, not the spacing it ought to have."""
    dates = pd.to_datetime(df["ds"]).sort_values()
    if len(dates) < 2:
        return pd.Timedelta(days=1)
    gaps = dates.diff().dropna()
    median = gaps.median()
    return median if median > pd.Timedelta(0) else pd.Timedelta(days=1)


class _Baseline(BaseForecastModel):
    """Shared fitting, interval estimation and holdout scoring.

    Subclasses supply two things: the point forecast for a horizon, and the
    one-step in-sample prediction used to estimate the spread. Keeping those
    together is deliberate -- an interval estimated from a *different* rule than
    the forecast would describe a method nobody is running.
    """

    def __init__(self, model_name: str, version: str = "1.0"):
        super().__init__(model_name, version)
        self._values: Optional[pd.Series] = None
        self._last_date: Optional[pd.Timestamp] = None
        self._step: pd.Timedelta = pd.Timedelta(days=1)
        self._sigma: float = 0.0
        self._n_errors: int = 0

    # -- to be provided by each baseline -------------------------------------

    def _point_forecast(self, values: pd.Series, horizon: int) -> np.ndarray:
        raise NotImplementedError

    def _one_step_predictions(self, values: pd.Series) -> np.ndarray:
        """Prediction for each position from the data before it; NaN where undefined."""
        raise NotImplementedError

    def _min_observations(self) -> int:
        return 2

    # -- interface -----------------------------------------------------------

    def fit(self, df: pd.DataFrame, target_col: str, **kwargs) -> None:
        values = _prepare(df, target_col)
        needed = self._min_observations()
        if len(values) < needed:
            raise ValueError(
                f"{self.model_name} needs at least {needed} observations, got {len(values)}"
            )

        self._values = values
        self._last_date = pd.to_datetime(df["ds"]).max()
        self._step = _infer_step(df)

        predicted = self._one_step_predictions(values)
        errors = values.to_numpy(dtype=float) - predicted
        errors = errors[~np.isnan(errors)]
        self._n_errors = int(errors.size)
        # ddof=1: the spread of a sample, not of a population. With one error
        # numpy would return nan, so that case reports zero spread and low
        # confidence rather than a nan interval.
        self._sigma = float(np.std(errors, ddof=1)) if errors.size > 1 else 0.0

    def predict(self, horizon: int) -> ForecastResult:
        if self._values is None or self._last_date is None:
            raise ValueError(f"{self.model_name} is not fitted")
        if horizon < 1:
            raise ValueError(f"horizon must be >= 1, got {horizon}")

        yhat = self._point_forecast(self._values, horizon)
        # Random-walk widening: independent one-step errors accumulate, so the
        # h-step spread grows with sqrt(h).
        spread = np.array([Z_90 * self._sigma * math.sqrt(h + 1) for h in range(horizon)])

        return ForecastResult(
            dates=_future_dates(self._last_date, horizon, self._step),
            yhat=[float(v) for v in yhat],
            yhat_lower=[float(v) for v in (yhat - spread)],
            yhat_upper=[float(v) for v in (yhat + spread)],
            model_name=self.model_name,
            model_version=self.version,
            confidence="high" if self._n_errors >= _THIN_HISTORY else "low",
            metadata={
                "baseline": True,
                "sigma_one_step": self._sigma,
                "errors_used": self._n_errors,
                "observations": int(len(self._values)),
                #: So a report cannot present a baseline as a trained model.
                "interval_method": "random_walk_sqrt_h",
                "interval_level": 0.90,
            },
        )

    def validate(self, df: pd.DataFrame, target_col: str) -> Dict[str, float]:
        """Score on the last 20%, as `BaseForecastModel` specifies.

        The split is by position in time, never at random: for a series, a random
        split lets the model see the future of its own test set, and every number
        it produces afterwards is meaningless. Principle 12.
        """
        values = _prepare(df, target_col)
        split = int(len(values) * 0.8)
        if split < self._min_observations() or split >= len(values):
            raise ValueError(
                f"{self.model_name}: not enough data to hold out a test set "
                f"({len(values)} observations)"
            )

        train, test = values.iloc[:split], values.iloc[split:]
        fitted = self.__class__(**self._reconstruct_kwargs())
        fitted.fit(
            pd.DataFrame({"ds": pd.to_datetime(df["ds"]).sort_values().iloc[:split].to_numpy(),
                          target_col: train.to_numpy()}),
            target_col,
        )
        predicted = fitted._point_forecast(train, len(test))
        actual = test.to_numpy(dtype=float)
        error = actual - predicted

        mae = float(np.mean(np.abs(error)))
        rmse = float(math.sqrt(np.mean(error ** 2)))
        # MAPE is undefined at zero, and environmental readings do hit zero.
        # Those points are excluded and counted rather than silently averaged in.
        nonzero = actual != 0
        mape = (
            float(np.mean(np.abs(error[nonzero] / actual[nonzero])) * 100.0)
            if nonzero.any() else float("nan")
        )
        return {
            "mae": mae,
            "rmse": rmse,
            "mape": mape,
            "test_samples": float(len(test)),
            "mape_points_excluded": float(int((~nonzero).sum())),
        }

    def _reconstruct_kwargs(self) -> Dict:
        return {}


class NaiveForecast(_Baseline):
    """Carry the last observation forward. The floor for a trending series."""

    def __init__(self):
        super().__init__("Naive", "1.0")

    def _point_forecast(self, values: pd.Series, horizon: int) -> np.ndarray:
        return np.full(horizon, float(values.iloc[-1]))

    def _one_step_predictions(self, values: pd.Series) -> np.ndarray:
        array = values.to_numpy(dtype=float)
        predicted = np.empty_like(array)
        predicted[0] = np.nan
        predicted[1:] = array[:-1]
        return predicted


class SeasonalNaiveForecast(_Baseline):
    """Carry the value from one season ago.

    The one that is hard to beat on environmental series. `season_length` is in
    observations, not days: 7 for a daily series with a weekly cycle, 24 for an
    hourly series with a daily one.
    """

    def __init__(self, season_length: int = 7):
        if season_length < 1:
            raise ValueError(f"season_length must be >= 1, got {season_length}")
        super().__init__(f"SeasonalNaive(m={season_length})", "1.0")
        self.season_length = season_length

    def _min_observations(self) -> int:
        # One full season plus one, or there is not a single seasonal error to
        # estimate the interval from.
        return self.season_length + 1

    def _point_forecast(self, values: pd.Series, horizon: int) -> np.ndarray:
        season = values.to_numpy(dtype=float)[-self.season_length:]
        return np.array([season[i % self.season_length] for i in range(horizon)])

    def _one_step_predictions(self, values: pd.Series) -> np.ndarray:
        array = values.to_numpy(dtype=float)
        predicted = np.full_like(array, np.nan)
        m = self.season_length
        if len(array) > m:
            predicted[m:] = array[:-m]
        return predicted

    def _reconstruct_kwargs(self) -> Dict:
        return {"season_length": self.season_length}


class MovingAverageForecast(_Baseline):
    """The mean of the last *k* observations. Beats naive on noise, loses on trend."""

    def __init__(self, window: int = 7):
        if window < 1:
            raise ValueError(f"window must be >= 1, got {window}")
        super().__init__(f"MovingAverage(k={window})", "1.0")
        self.window = window

    def _min_observations(self) -> int:
        return self.window + 1

    def _point_forecast(self, values: pd.Series, horizon: int) -> np.ndarray:
        mean = float(values.to_numpy(dtype=float)[-self.window:].mean())
        return np.full(horizon, mean)

    def _one_step_predictions(self, values: pd.Series) -> np.ndarray:
        array = values.to_numpy(dtype=float)
        predicted = np.full_like(array, np.nan)
        for i in range(self.window, len(array)):
            predicted[i] = array[i - self.window:i].mean()
        return predicted

    def _reconstruct_kwargs(self) -> Dict:
        return {"window": self.window}


class ClimatologyForecast(_Baseline):
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

    Speed: fit records where every date sits in the series, once. A forecast then
    looks up its window dates instead of scanning the series for each one, so it
    costs O(years x 7). The values pooled, and their order, are those the scan
    produced; tests/test_forecasting_climatology_equivalence.py checks this exactly.
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
        # date -> positions in self._dates, ascending. Built once per fit.
        self._positions: Dict[dt_date, List[int]] = {}
        # Distinct calendar years in the fitted history, ascending.
        self._years: List[int] = []

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
        self._index_dates()

        # For interval estimation, compute one-step errors
        predicted = self._one_step_predictions(values)
        errors = values.to_numpy(dtype=float) - predicted
        errors = errors[~np.isnan(errors)]
        self._n_errors = int(errors.size)
        self._sigma = float(np.std(errors, ddof=1)) if errors.size > 1 else 0.0

    def _index_dates(self) -> None:
        """Record where each date sits in the fitted series.

        Positions for a date are kept ascending, so pooling them yields values in
        the order a scan over the series would have yielded them.
        """
        dates_array = self._dates.dt.date.to_numpy()
        positions: Dict[dt_date, List[int]] = {}
        for position, day in enumerate(dates_array):
            positions.setdefault(day, []).append(position)
        self._positions = positions
        self._years = sorted({day.year for day in dates_array})

    def _pool(self, values_array: np.ndarray, target: dt_date, origin: dt_date,
              before: Optional[int] = None) -> Tuple[List[float], set]:
        """Pool the observations of the earlier years that qualify for one target day.

        A year Y qualifies if Y is earlier than the target's year and every date of
        its window is on or before `origin`. The window is anchor-3 .. anchor+3,
        the anchor being the target's month-day in Y (29 Feb -> 28 Feb). Only
        positions below `before` count, when given: a one-step prediction must not
        use the day it is predicting.

        Returns the pooled values, and the years that contributed at least one.
        """
        anchor_month, anchor_day = target.month, target.day
        if anchor_month == 2 and anchor_day == 29:
            anchor_month, anchor_day = 2, 28

        pooled: List[float] = []
        contributed: set = set()
        for year in self._years:
            if year >= target.year:
                continue
            try:
                anchor = dt_date(year, anchor_month, anchor_day)
            except ValueError:
                continue

            # The window crosses year boundaries: 1 Jan pools 29 Dec Y-1 .. 4 Jan Y.
            window_dates = [anchor + timedelta(days=offset)
                            for offset in range(-self.window_days, self.window_days + 1)]

            # All 7 dates must be <= origin for this year to qualify
            if any(d > origin for d in window_dates):
                continue

            for window_date in window_dates:
                for position in self._positions.get(window_date, ()):
                    if before is not None and position >= before:
                        continue
                    pooled.append(float(values_array[position]))
                    contributed.add(year)
        return pooled, contributed

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
        values_array = values.to_numpy(dtype=float)
        forecasts = []

        for h in range(horizon):
            forecast_date = (self._last_date + self._step * (h + 1)).date()
            pooled, contributed = self._pool(values_array, forecast_date, origin)

            if len(contributed) < 2:
                raise ValueError(
                    f"Climatology: only {len(contributed)} earlier year(s) found for "
                    f"{forecast_date.isoformat()}, need at least 2"
                )

            if not pooled:
                raise ValueError(
                    f"Climatology: no values collected for {forecast_date.isoformat()}"
                )

            forecasts.append(float(np.mean(pooled)))

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
            # The origin for point i is the day before it.
            pooled, contributed = self._pool(
                array, dates_array[i], dates_array[i - 1], before=i,
            )
            if len(contributed) >= 2 and pooled:
                predicted[i] = float(np.mean(pooled))

        return predicted

    def _reconstruct_kwargs(self) -> Dict:
        return {"window_days": self.window_days}


#: What a challenger is compared against by default. Built here rather than at
#: each call site so a comparison cannot quietly omit the one that would have won.
def default_baselines(season_length: int = 7, window: int = 7) -> List[_Baseline]:
    return [
        NaiveForecast(),
        SeasonalNaiveForecast(season_length=season_length),
        MovingAverageForecast(window=window),
    ]
