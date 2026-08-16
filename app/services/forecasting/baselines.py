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
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from app.services.forecasting.base import BaseForecastModel, ForecastResult

#: 1.645 sigma is the 90% two-sided normal interval, matching the 90% that
#: `ForecastResult` documents for yhat_lower/yhat_upper.
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


#: What a challenger is compared against by default. Built here rather than at
#: each call site so a comparison cannot quietly omit the one that would have won.
def default_baselines(season_length: int = 7, window: int = 7) -> List[_Baseline]:
    return [
        NaiveForecast(),
        SeasonalNaiveForecast(season_length=season_length),
        MovingAverageForecast(window=window),
    ]
