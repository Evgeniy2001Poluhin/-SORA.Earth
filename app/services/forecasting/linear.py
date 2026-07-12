"""Linear trend forecaster — lightweight fallback that always works."""

import numpy as np
import pandas as pd
from typing import Dict
from datetime import timedelta
import logging

from .base import BaseForecastModel, ForecastResult

log = logging.getLogger(__name__)


class LinearTrendForecaster(BaseForecastModel):
    """Simple linear regression forecaster.

    Always succeeds with as few as 3 data points. Used as the final
    fallback in the ensemble chain and as the cold-start model when
    historical data is minimal.
    """

    def __init__(self):
        super().__init__("LinearTrend", "1.0")
        self._coeffs = None
        self._sigma = 0.0
        self._t0 = None
        self._last_day = 0

    def fit(self, df: pd.DataFrame, target_col: str, **kwargs) -> None:
        if len(df) < 3:
            raise ValueError(f"Need at least 3 rows, got {len(df)}")

        self._t0 = df["ds"].min()
        x = (df["ds"] - self._t0).dt.days.to_numpy(dtype=float)
        y = df[target_col].to_numpy(dtype=float)

        self._coeffs = np.polyfit(x, y, 1)
        fit_fn = np.poly1d(self._coeffs)
        resid = y - fit_fn(x)
        self._sigma = float(np.std(resid, ddof=2)) if len(y) > 2 else 0.0
        self._last_day = int(x.max())

    def predict(self, horizon: int) -> ForecastResult:
        if self._coeffs is None:
            raise ValueError("Model not fitted")

        fit_fn = np.poly1d(self._coeffs)
        ci = 1.96 * self._sigma

        fut_x = np.arange(self._last_day + 1, self._last_day + 1 + horizon, dtype=float)
        yhat = fit_fn(fut_x)

        dates = [(self._t0 + timedelta(days=int(fx))).strftime("%Y-%m-%d") for fx in fut_x]

        return ForecastResult(
            dates=dates,
            yhat=yhat.tolist(),
            yhat_lower=(yhat - ci).tolist(),
            yhat_upper=(yhat + ci).tolist(),
            model_name=self.model_name,
            model_version=self.version,
            confidence="low",
            metadata={
                "slope_per_day": round(float(self._coeffs[0]), 5),
                "ci95": round(ci, 3),
            }
        )

    def validate(self, df: pd.DataFrame, target_col: str) -> Dict[str, float]:
        from sklearn.metrics import mean_absolute_error, mean_squared_error

        try:
            split_idx = int(len(df) * 0.8)
            train_df = df.iloc[:split_idx]
            test_df = df.iloc[split_idx:]

            if len(test_df) < 2 or len(train_df) < 3:
                return {"mae": 0.0, "rmse": 0.0, "mape": 0.0}

            self.fit(train_df, target_col)
            result = self.predict(len(test_df))
            y_pred = np.array(result.yhat)
            y_true = test_df[target_col].values[:len(y_pred)]

            mae = mean_absolute_error(y_true, y_pred)
            rmse = np.sqrt(mean_squared_error(y_true, y_pred))
            mape = np.mean(np.abs((y_true - y_pred) / (y_true + 1e-10))) * 100

            return {"mae": float(mae), "rmse": float(rmse), "mape": float(mape)}
        except Exception as e:
            log.error(f"LinearTrend validation failed: {e}")
            return {"mae": float('inf'), "rmse": float('inf'), "mape": float('inf')}
