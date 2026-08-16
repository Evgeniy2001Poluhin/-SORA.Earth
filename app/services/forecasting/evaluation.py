"""Rolling-origin evaluation: the only way a forecast claim here may be made (#199 phase 6).

`baselines.py` gave models something to lose to. This gives them somewhere to
lose it. Measured 2026-08-16: nothing in this package computed MASE, interval
coverage or a confidence interval on a metric -- `tests/test_forecast_metrics.py`
covers a database table that *stores* metrics and the endpoints that read it,
never their computation.

## Why rolling origin and not a holdout

A single holdout gives one number from one accident of where the split landed.
The §7 gate in `entry_conditions.py` requires twelve windows for exactly this
reason, and the M3 declaration commits to it. Each window here trains on data
strictly before its test period and is scored on the horizon that follows, so
the arrangement is the one the gate will be argued from rather than a friendlier
one chosen afterwards.

## MASE, and the mistake it invites

MASE divides by the mean absolute error of a seasonal-naive one-step forecast
**computed on the training data of that window**. Scaling by the test set
instead is the classic error: it lets the denominator move with the thing being
measured, so a model looks better precisely where the test period happens to be
volatile. `_seasonal_scale` takes the training slice and nothing else, and a
test asserts that a model scored against a deliberately volatile test period
keeps the same MASE.

MASE < 1 means the model beat seasonal naive over that window. That is the
number principle 14 is about.

## What this does not do

No regional slicing. The roadmap's phase 6 asks for a worst-region report, and
that needs the region dimension threaded through, which is not attempted here --
stated rather than half-built.
"""
import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd


@dataclass
class WindowResult:
    """One origin: what was trained on, what was scored, and how it did."""

    window: int
    train_end: int
    test_start: int
    test_end: int
    mae: float
    rmse: float
    mase: float
    bias: float
    interval_coverage: Optional[float]


@dataclass
class EvaluationReport:
    """Every window kept, not only the average.

    The mean alone hides the case the gate cares about -- a model that is
    excellent in ten windows and catastrophic in two is not a model to promote,
    and averaging says it is fine.
    """

    model_name: str
    horizon: int
    windows: List[WindowResult] = field(default_factory=list)

    def _values(self, metric: str) -> np.ndarray:
        return np.array(
            [getattr(w, metric) for w in self.windows if getattr(w, metric) is not None],
            dtype=float,
        )

    def mean(self, metric: str = "mae") -> float:
        values = self._values(metric)
        return float(values.mean()) if values.size else float("nan")

    def worst(self, metric: str = "mae") -> float:
        values = self._values(metric)
        return float(values.max()) if values.size else float("nan")

    def confidence_interval(self, metric: str = "mae", level: float = 0.95,
                            resamples: int = 2000, seed: int = 0) -> Dict[str, float]:
        """Bootstrap CI over the per-window values.

        Twelve windows is a small sample, and reporting a mean from twelve
        numbers as though it were precise is how 0.81 on 171 rows came to be
        treated as clearing 0.80. The interval is over windows, so it answers
        "how much would this move on a different twelve" -- not "how much would
        it move on different data", which needs more than this.
        """
        values = self._values(metric)
        if values.size < 2:
            return {"low": float("nan"), "high": float("nan"), "n_windows": float(values.size)}

        rng = np.random.default_rng(seed)
        means = np.array([
            rng.choice(values, size=values.size, replace=True).mean()
            for _ in range(resamples)
        ])
        tail = (1.0 - level) / 2.0
        return {
            "low": float(np.quantile(means, tail)),
            "high": float(np.quantile(means, 1.0 - tail)),
            "n_windows": float(values.size),
        }

    def beats(self, other: "EvaluationReport", metric: str = "mae") -> bool:
        """Lower is better for every metric here. Ties are not wins."""
        mine, theirs = self.mean(metric), other.mean(metric)
        if math.isnan(mine) or math.isnan(theirs):
            return False
        return mine < theirs


def _seasonal_scale(train: np.ndarray, season_length: int) -> float:
    """Mean absolute seasonal-naive one-step error over the TRAINING data.

    The denominator must not see the test period; see the module docstring.
    """
    if train.size <= season_length:
        raise ValueError(
            f"need more than {season_length} training points to scale MASE, got {train.size}"
        )
    diffs = np.abs(train[season_length:] - train[:-season_length])
    scale = float(diffs.mean())
    if scale == 0.0:
        # A perfectly periodic training slice. Dividing by it would produce inf
        # or nan and quietly poison every average downstream.
        raise ValueError("seasonal scale is zero: the training slice is perfectly periodic")
    return scale


def rolling_origin_windows(n_observations: int, train_size: int, horizon: int,
                           n_windows: int, step: Optional[int] = None) -> List[Dict[str, int]]:
    """Index splits where every test period follows its own training data.

    `step` defaults to the horizon, so windows do not overlap and each test
    point is scored once. Raises rather than silently returning fewer windows
    than asked for: a report over eight windows labelled twelve is the kind of
    quiet shortfall the §7 gate exists to prevent.
    """
    if train_size < 1 or horizon < 1 or n_windows < 1:
        raise ValueError("train_size, horizon and n_windows must all be >= 1")
    step = horizon if step is None else step
    if step < 1:
        raise ValueError(f"step must be >= 1, got {step}")

    needed = train_size + horizon + step * (n_windows - 1)
    if n_observations < needed:
        raise ValueError(
            f"{n_windows} windows at horizon {horizon} need {needed} observations, "
            f"have {n_observations}"
        )

    windows = []
    for index in range(n_windows):
        train_end = train_size + step * index
        windows.append({
            "window": index,
            "train_end": train_end,
            "test_start": train_end,
            "test_end": train_end + horizon,
        })
    return windows


def evaluate_rolling_origin(
    fit_predict: Callable[[pd.DataFrame, int], Sequence[float]],
    df: pd.DataFrame,
    target_col: str,
    horizon: int,
    train_size: int,
    n_windows: int,
    season_length: int = 7,
    model_name: str = "model",
    interval: Optional[Callable[[pd.DataFrame, int], Sequence[Sequence[float]]]] = None,
) -> EvaluationReport:
    """Score `fit_predict` over rolling origins.

    `fit_predict(train_df, horizon)` must return `horizon` point forecasts using
    only the rows it is given. That signature is the whole contract: a callable
    that reaches past its argument for future rows is the leakage this design
    cannot detect, and the point-in-time helpers in `app/services/point_in_time.py`
    exist for that half of the problem.
    """
    if "ds" not in df.columns or target_col not in df.columns:
        raise ValueError("df must carry 'ds' and the target column")

    frame = df[["ds", target_col]].copy()
    frame["ds"] = pd.to_datetime(frame["ds"])
    frame = frame.dropna(subset=[target_col]).sort_values("ds").reset_index(drop=True)
    values = pd.to_numeric(frame[target_col], errors="coerce").to_numpy(dtype=float)

    report = EvaluationReport(model_name=model_name, horizon=horizon)
    for split in rolling_origin_windows(len(frame), train_size, horizon, n_windows):
        train_df = frame.iloc[:split["train_end"]]
        actual = values[split["test_start"]:split["test_end"]]

        predicted = np.asarray(list(fit_predict(train_df, horizon)), dtype=float)
        if predicted.shape != actual.shape:
            raise ValueError(
                f"window {split['window']}: expected {actual.shape[0]} forecasts, "
                f"got {predicted.shape[0]}"
            )

        error = actual - predicted
        scale = _seasonal_scale(values[:split["train_end"]], season_length)

        coverage = None
        if interval is not None:
            bounds = np.asarray(list(interval(train_df, horizon)), dtype=float)
            if bounds.shape != (horizon, 2):
                raise ValueError(
                    f"window {split['window']}: interval must be {horizon}x2, got {bounds.shape}"
                )
            inside = (actual >= bounds[:, 0]) & (actual <= bounds[:, 1])
            coverage = float(inside.mean())

        report.windows.append(WindowResult(
            window=split["window"],
            train_end=split["train_end"],
            test_start=split["test_start"],
            test_end=split["test_end"],
            mae=float(np.mean(np.abs(error))),
            rmse=float(math.sqrt(np.mean(error ** 2))),
            mase=float(np.mean(np.abs(error)) / scale),
            # Signed on purpose: a model that is wrong by 5 in both directions
            # and one that is always 5 low have the same MAE and are different
            # problems.
            bias=float(np.mean(error)),
            interval_coverage=coverage,
        ))

    return report
