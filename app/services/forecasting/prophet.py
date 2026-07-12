"""Prophet forecaster for seasonal decomposition and trend analysis."""

import pandas as pd
import numpy as np
from typing import Dict
import logging

from .base import BaseForecastModel, ForecastResult

log = logging.getLogger(__name__)


class ProphetForecaster(BaseForecastModel):
    """Prophet forecaster with Bayesian uncertainty intervals.

    Uses Facebook Prophet for:
    - Automatic seasonal decomposition (yearly, weekly)
    - Trend detection with changepoint analysis
    - Bayesian confidence intervals (1000 samples)

    Fallback model when LSTM fails or for small datasets.
    """

    def __init__(self):
        super().__init__("Prophet", "1.0")
        self.model = None

    def fit(self, df: pd.DataFrame, target_col: str, **kwargs) -> None:
        """Fit Prophet model.

        Args:
            df: DataFrame with 'ds' (date) and target column
            target_col: Target column name
            **kwargs: Ignored (Prophet doesn't use external regressors here)

        Raises:
            ImportError: If prophet not installed
            ValueError: If insufficient data (< 10 rows)
        """
        try:
            from prophet import Prophet
        except ImportError:
            raise ImportError(
                "Prophet not installed. Install with: pip install prophet\n"
                "Note: Prophet requires pystan, which may need additional setup."
            )

        if len(df) < 10:
            raise ValueError(f"Insufficient data: {len(df)} rows, need at least 10")

        # Prophet requires 'ds' (date) and 'y' (target) columns
        prophet_df = df[["ds", target_col]].copy()
        prophet_df.columns = ["ds", "y"]

        # Ensure ds is datetime
        prophet_df["ds"] = pd.to_datetime(prophet_df["ds"])

        # Initialize Prophet with reasonable defaults for ESG metrics
        self.model = Prophet(
            yearly_seasonality=True,
            weekly_seasonality=True,
            daily_seasonality=False,
            uncertainty_samples=1000,  # For robust confidence intervals
            changepoint_prior_scale=0.05  # Conservative changepoint detection
        )

        # Suppress Prophet's verbose logging
        import logging as prophet_log
        prophet_log.getLogger('prophet').setLevel(prophet_log.WARNING)

        log.info(f"Fitting Prophet on {len(prophet_df)} data points...")
        self.model.fit(prophet_df)
        log.info("Prophet fitted successfully")

    def predict(self, horizon: int) -> ForecastResult:
        """Generate forecast with Bayesian uncertainty intervals.

        Args:
            horizon: Number of days ahead to forecast

        Returns:
            ForecastResult with predictions and 80% confidence intervals
                (Prophet uses 80% by default, we'll map to 90% for consistency)

        Raises:
            ValueError: If model not fitted
        """
        if self.model is None:
            raise ValueError("Model not fitted. Call fit() first.")

        # Generate future dataframe
        future = self.model.make_future_dataframe(periods=horizon, freq='D')
        forecast = self.model.predict(future)

        # Extract only future predictions (last `horizon` rows)
        forecast_only = forecast.tail(horizon).copy()

        # Prophet returns 80% CI by default, adjust to approximate 90% CI
        # Rough approximation: widen by factor of 1.645/1.282 ≈ 1.28
        ci_adjustment = 1.28
        ci_width = (forecast_only["yhat_upper"] - forecast_only["yhat_lower"]) / 2
        forecast_only["yhat_lower_90"] = forecast_only["yhat"] - ci_width * ci_adjustment
        forecast_only["yhat_upper_90"] = forecast_only["yhat"] + ci_width * ci_adjustment

        # Assess confidence (narrow interval = high confidence)
        avg_ci_width = (forecast_only["yhat_upper_90"] - forecast_only["yhat_lower_90"]).mean()
        yhat_mean = forecast_only["yhat"].mean()

        if yhat_mean > 0:
            relative_width = avg_ci_width / yhat_mean
            confidence = "high" if relative_width < 0.2 else "medium" if relative_width < 0.5 else "low"
        else:
            confidence = "medium"

        log.info(f"Prophet forecast: horizon={horizon}, confidence={confidence}, avg_CI={avg_ci_width:.3f}")

        return ForecastResult(
            dates=forecast_only["ds"].dt.strftime("%Y-%m-%d").tolist(),
            yhat=forecast_only["yhat"].tolist(),
            yhat_lower=forecast_only["yhat_lower_90"].tolist(),
            yhat_upper=forecast_only["yhat_upper_90"].tolist(),
            model_name=self.model_name,
            model_version=self.version,
            confidence=confidence,
            metadata={
                "avg_ci_width": float(avg_ci_width),
                "uncertainty_samples": 1000
            }
        )

    def validate(self, df: pd.DataFrame, target_col: str) -> Dict[str, float]:
        """Compute validation metrics on holdout set (last 20%).

        Args:
            df: Full dataset
            target_col: Target column name

        Returns:
            Dict with MAE, RMSE, MAPE
        """
        from sklearn.metrics import mean_absolute_error, mean_squared_error

        try:
            split_idx = int(len(df) * 0.8)
            train_df = df.iloc[:split_idx]
            test_df = df.iloc[split_idx:]

            if len(test_df) < 5:
                log.warning("Test set too small for validation")
                return {"mae": 0.0, "rmse": 0.0, "mape": 0.0}

            self.fit(train_df, target_col)

            # Predict on test dates
            future = pd.DataFrame({"ds": pd.to_datetime(test_df["ds"])})
            forecast = self.model.predict(future)

            y_true = test_df[target_col].values
            y_pred = forecast["yhat"].values

            mae = mean_absolute_error(y_true, y_pred)
            rmse = np.sqrt(mean_squared_error(y_true, y_pred))
            mape = np.mean(np.abs((y_true - y_pred) / (y_true + 1e-10))) * 100

            log.info(f"Prophet validation: MAE={mae:.3f}, RMSE={rmse:.3f}, MAPE={mape:.1f}%")

            return {"mae": float(mae), "rmse": float(rmse), "mape": float(mape)}
        except Exception as e:
            log.error(f"Prophet validation failed: {e}")
            return {"mae": float('inf'), "rmse": float('inf'), "mape": float('inf')}
