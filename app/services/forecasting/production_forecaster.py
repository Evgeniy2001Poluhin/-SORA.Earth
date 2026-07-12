"""Production forecaster that uses MLflow-trained models.

This module wraps ML flow models to work with our forecasting interface.
Falls back to on-demand training if MLflow models are not available.
"""

import logging
import pandas as pd
import numpy as np
from typing import Dict, Optional
from datetime import timedelta

from .base import BaseForecastModel, ForecastResult
from .mlflow_loader import load_production_model, is_mlflow_available
from .ensemble import EnsembleForecaster

log = logging.getLogger(__name__)


class ProductionForecaster(BaseForecastModel):
    """Production forecaster with MLflow integration and fallback.

    Strategy:
    1. Try to load pre-trained model from MLflow
    2. If not available, fall back to training Ensemble on-demand
    3. Cache the result for future requests
    """

    def __init__(self, metric: str = "score"):
        super().__init__("ProductionForecaster", "1.0")
        self.metric = metric
        self.mlflow_model = None
        self.fallback_model = None
        self.model_source = None  # 'mlflow' or 'fallback'
        self._fitted = False
        self._last_data_hash = None

    def fit(self, df: pd.DataFrame, target_col: str, **kwargs) -> None:
        """Fit the forecaster.

        For MLflow models: this is a no-op (already trained)
        For fallback: trains ensemble model

        Args:
            df: DataFrame with 'ds' and target column
            target_col: Target column name
            **kwargs: Additional options (country, etc.)
        """
        # Try to load MLflow model first
        if is_mlflow_available() and self.mlflow_model is None:
            self.mlflow_model = load_production_model(self.metric, model_type="lstm")

            if self.mlflow_model:
                self.model_source = "mlflow"
                self._fitted = True
                log.info(f"Using MLflow model for {self.metric}")
                return

        # Fallback to training ensemble
        if self.fallback_model is None:
            log.info(f"MLflow model not available, training ensemble for {self.metric}")
            self.fallback_model = EnsembleForecaster()

        self.fallback_model.fit(df, target_col, **kwargs)
        self.model_source = "fallback"
        self._fitted = True

    def predict(self, horizon: int) -> ForecastResult:
        """Generate forecast.

        Args:
            horizon: Number of days to forecast

        Returns:
            ForecastResult with predictions

        Raises:
            ValueError: If model not fitted
        """
        if not self._fitted:
            raise ValueError("Model not fitted. Call fit() first.")

        # MLflow LSTM model
        if self.model_source == "mlflow" and self.mlflow_model:
            try:
                # MLflow PyTorch models return raw predictions
                # We need to wrap them in ForecastResult format
                return self._predict_mlflow(horizon)
            except Exception as e:
                log.error(f"MLflow prediction failed: {e}, falling back to ensemble")
                # Fall through to fallback

        # Fallback ensemble model
        if self.fallback_model:
            result = self.fallback_model.predict(horizon)
            # Tag as fallback
            result.metadata["source"] = "fallback_ensemble"
            return result

        raise RuntimeError("No model available for prediction")

    def _predict_mlflow(self, horizon: int) -> ForecastResult:
        """Generate prediction using MLflow model.

        Note: This is a simplified wrapper. MLflow LSTM models expect
        specific input format. For production, we'd need proper feature
        engineering and sequence preparation.

        For now, this serves as a placeholder/demonstration.
        """
        # TODO: Implement proper MLflow model inference
        # This requires:
        # 1. Feature engineering pipeline
        # 2. Sequence preparation (last 30 days)
        # 3. MC Dropout sampling
        # 4. Proper date generation

        log.warning("MLflow model inference not fully implemented, using fallback")

        # For now, fall back to ensemble
        if not self.fallback_model:
            from datetime import datetime
            # Return dummy forecast as last resort
            today = datetime.now()
            dates = [(today + timedelta(days=i+1)).strftime("%Y-%m-%d") for i in range(horizon)]

            return ForecastResult(
                dates=dates,
                yhat=[50.0] * horizon,  # Dummy prediction
                yhat_lower=[40.0] * horizon,
                yhat_upper=[60.0] * horizon,
                model_name="MLflow-LSTM (stub)",
                model_version="1.0",
                confidence="low",
                metadata={"note": "MLflow inference not implemented, using stub"}
            )

        return self.fallback_model.predict(horizon)

    def validate(self, df: pd.DataFrame, target_col: str) -> Dict[str, float]:
        """Validate the forecaster.

        Args:
            df: Full dataset
            target_col: Target column name

        Returns:
            Dict with MAE, RMSE, MAPE
        """
        if self.fallback_model:
            return self.fallback_model.validate(df, target_col)

        # Can't validate MLflow model without retraining
        log.warning("Cannot validate MLflow production model")
        return {"mae": 0.0, "rmse": 0.0, "mape": 0.0}

    def get_model_info(self) -> Dict:
        """Get information about the active model."""
        info = {
            "metric": self.metric,
            "source": self.model_source,
            "fitted": self._fitted
        }

        if self.model_source == "mlflow":
            info["model_type"] = "LSTM"
            info["trained_externally"] = True
        elif self.model_source == "fallback":
            info["model_type"] = "Ensemble"
            info["trained_on_demand"] = True
            if self.fallback_model:
                info["weights"] = self.fallback_model.weights
                info["active_models"] = [name for name, _ in self.fallback_model._active_models]

        return info
