"""Candidate forecast models for phase 9 -- the first challengers to compare with baselines.

There is no champion for the M3 target (daily temperature over the 21 declared
regions) yet. `docs/DEVELOPMENT_ROADMAP.md` phase 9 says "Challenger compared to
champion and baseline over identical windows"; since there is no champion, the
first candidates are compared with the baselines.

Candidates:
- `linear_trend`: LinearTrendForecaster from `linear.py`
- `xgboost_lag`: XGBoostLagForecaster (tabular regressor on lag features)
- `lstm`: LSTMForecaster from `lstm.py` (shadow only)

**Shadow models** are evaluated only on request (with `include_shadow=True`) and
are never eligible for promotion, as the roadmap says. They are there to be
measured, not to become champion.
"""
import math
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from datetime import timedelta

from .base import BaseForecastModel, ForecastResult


@dataclass
class CandidateSpec:
    """A named candidate with its factory and role."""

    name: str
    factory: callable  # () -> BaseForecastModel
    role: str  # "candidate" or "shadow"


class XGBoostLagForecaster(BaseForecastModel):
    """XGBoost regressor on lag features for daily time series.

    Features:
    - Lags 1..2×season_length (e.g., lags 1-14 for weekly seasonality)
    - Day-of-week (0=Monday, 6=Sunday)
    - Rolling means over [season_length, 2×season_length] computed from past
      values only (no leakage)

    Forecast recursively for multi-step horizons: each step's prediction becomes
    the lag-1 feature for the next, and rolling means are updated accordingly.

    Deterministic: fixed random_state and n_jobs=1.

    Intervals: the 90% interval comes from the empirical 5th/95th percentiles of
    the in-sample one-step residuals. This is an approximation that ignores error
    growth over the horizon -- a real quantile regression would model that, but
    this is the baseline approximation every tabular model gets until a better one
    is built.
    """

    def __init__(self, season_length: int = 7, random_state: int = 42):
        super().__init__("XGBoostLag", "1.0")
        self.season_length = season_length
        self.random_state = random_state
        self._model = None
        self._history = None
        self._last_date = None
        self._step = None
        self._p5 = 0.0
        self._p95 = 0.0

    def _min_observations(self) -> int:
        """Enough for the longest lag plus one to score."""
        return 2 * self.season_length + 1

    def fit(self, df: pd.DataFrame, target_col: str, **kwargs) -> None:
        import xgboost as xgb

        if "ds" not in df.columns:
            raise ValueError("df must carry a 'ds' date column")
        if target_col not in df.columns:
            raise ValueError(f"df has no column {target_col!r}")

        frame = df[["ds", target_col]].copy()
        frame["ds"] = pd.to_datetime(frame["ds"])
        frame = frame.dropna(subset=[target_col]).sort_values("ds").reset_index(drop=True)

        needed = self._min_observations()
        if len(frame) < needed:
            raise ValueError(
                f"XGBoostLag needs at least {needed} observations, got {len(frame)}"
            )

        values = pd.to_numeric(frame[target_col], errors="coerce").to_numpy(dtype=float)
        self._history = values.copy()
        self._last_date = frame["ds"].iloc[-1]

        # Infer step from median gap
        dates = frame["ds"]
        if len(dates) >= 2:
            gaps = dates.diff().dropna()
            median = gaps.median()
            self._step = median if median > pd.Timedelta(0) else pd.Timedelta(days=1)
        else:
            self._step = pd.Timedelta(days=1)

        # Build features -- strictly from past values
        X, y = self._build_features(values)

        # Train XGBoost with balanced regularization
        # Must learn seasonal patterns while not overfitting to noise
        self._model = xgb.XGBRegressor(
            n_estimators=100,
            max_depth=4,  # Enough depth to capture interactions
            learning_rate=0.1,
            min_child_weight=3,  # Moderate regularization
            subsample=0.8,  # Stochastic sampling
            colsample_bytree=0.8,
            reg_lambda=1.0,  # L2 regularization only
            random_state=self.random_state,
            n_jobs=1,
            verbosity=0,
        )
        self._model.fit(X, y)

        # Estimate intervals from in-sample one-step residuals
        y_pred = self._model.predict(X)
        residuals = y - y_pred
        self._p5 = float(np.percentile(residuals, 5))
        self._p95 = float(np.percentile(residuals, 95))

    def _build_features(self, values: np.ndarray) -> tuple:
        """Build lag features, day-of-week, and rolling means from values.

        Returns (X, y) where X is (n_samples, n_features) and y is the targets.
        Only rows with all lags available are included.
        """
        max_lag = 2 * self.season_length
        if len(values) < max_lag + 1:
            raise ValueError(f"need at least {max_lag + 1} values for feature building")

        rows = []
        targets = []

        for i in range(max_lag, len(values)):
            # Lags 1..2×season_length
            lags = [values[i - lag] for lag in range(1, max_lag + 1)]

            # Day-of-week (cyclical pattern, 0=Monday)
            # We don't have the actual date in this version, so we use
            # position % 7 as a proxy for daily seasonality
            dow = i % 7

            # Rolling means over [season_length, 2×season_length]
            # Computed from PAST values only (values before position i)
            ma_short = float(np.mean(values[i - self.season_length : i]))
            ma_long = float(np.mean(values[i - 2 * self.season_length : i]))

            features = lags + [dow, ma_short, ma_long]
            rows.append(features)
            targets.append(values[i])

        return np.array(rows, dtype=float), np.array(targets, dtype=float)

    def predict(self, horizon: int) -> ForecastResult:
        if self._model is None or self._history is None:
            raise ValueError("Model not fitted. Call fit() first.")
        if horizon < 1:
            raise ValueError(f"horizon must be >= 1, got {horizon}")

        # Recursive forecasting: each step's prediction becomes the next lag-1
        buffer = list(self._history)
        predictions = []

        for step in range(horizon):
            # Build features from the buffer
            max_lag = 2 * self.season_length
            lags = [buffer[-(lag)] for lag in range(1, max_lag + 1)]

            # Day-of-week from current position
            dow = (len(buffer)) % 7

            # Rolling means from buffer
            ma_short = float(np.mean(buffer[-self.season_length :]))
            ma_long = float(np.mean(buffer[-2 * self.season_length :]))

            features = lags + [dow, ma_short, ma_long]
            X_step = np.array([features], dtype=float)

            # Predict
            pred = float(self._model.predict(X_step)[0])
            predictions.append(pred)

            # Add to buffer for next step
            buffer.append(pred)

        # Build dates
        dates = [
            (self._last_date + self._step * (i + 1)).strftime("%Y-%m-%d")
            for i in range(horizon)
        ]

        # Intervals from empirical percentiles (fixed, no widening)
        yhat = np.array(predictions)
        yhat_lower = yhat + self._p5
        yhat_upper = yhat + self._p95

        return ForecastResult(
            dates=dates,
            yhat=yhat.tolist(),
            yhat_lower=yhat_lower.tolist(),
            yhat_upper=yhat_upper.tolist(),
            model_name=self.model_name,
            model_version=self.version,
            confidence="medium",
            metadata={
                "season_length": self.season_length,
                "max_lag": 2 * self.season_length,
                "interval_method": "empirical_percentiles_5_95",
                "interval_note": "approximation ignoring error growth over horizon",
                "recursive": True,
            },
        )

    def validate(self, df: pd.DataFrame, target_col: str) -> Dict[str, float]:
        """Score on the last 20% of the data."""
        if "ds" not in df.columns:
            raise ValueError("df must carry a 'ds' date column")
        if target_col not in df.columns:
            raise ValueError(f"df has no column {target_col!r}")

        frame = df[["ds", target_col]].copy()
        frame["ds"] = pd.to_datetime(frame["ds"])
        frame = frame.dropna(subset=[target_col]).sort_values("ds").reset_index(drop=True)

        values = pd.to_numeric(frame[target_col], errors="coerce").to_numpy(dtype=float)
        split = int(len(values) * 0.8)

        if split < self._min_observations() or split >= len(values):
            raise ValueError(
                f"not enough data to hold out a test set ({len(values)} observations)"
            )

        train, test = values[:split], values[split:]

        # Fit on train
        train_df = pd.DataFrame(
            {"ds": frame["ds"].iloc[:split].to_numpy(), target_col: train}
        )
        self.fit(train_df, target_col)

        # Predict on test
        result = self.predict(len(test))
        y_pred = np.array(result.yhat)
        y_true = test[: len(y_pred)]

        mae = float(np.mean(np.abs(y_true - y_pred)))
        rmse = float(math.sqrt(np.mean((y_true - y_pred) ** 2)))

        # MAPE, excluding zeros
        nonzero = y_true != 0
        if nonzero.any():
            mape = float(
                np.mean(np.abs((y_true[nonzero] - y_pred[nonzero]) / y_true[nonzero]))
                * 100.0
            )
        else:
            mape = float("nan")

        return {
            "mae": mae,
            "rmse": rmse,
            "mape": mape,
            "test_samples": float(len(test)),
        }


def _make_linear():
    from .linear import LinearTrendForecaster

    return LinearTrendForecaster()


def _make_xgboost():
    return XGBoostLagForecaster(season_length=7, random_state=42)


def _make_lstm():
    from .lstm import LSTMForecaster

    return LSTMForecaster(seq_length=14, hidden_size=128, dropout=0.2, mc_samples=50)


#: The declared candidates, in order. Each has a name, factory and role.
#: "candidate" = eligible for promotion if it beats baselines.
#: "shadow" = evaluated only on request, never eligible.
CANDIDATES: List[CandidateSpec] = [
    CandidateSpec(name="linear_trend", factory=_make_linear, role="candidate"),
    CandidateSpec(name="xgboost_lag", factory=_make_xgboost, role="candidate"),
    CandidateSpec(name="lstm", factory=_make_lstm, role="shadow"),
]
