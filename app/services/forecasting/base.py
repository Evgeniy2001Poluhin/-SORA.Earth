"""Base classes for time series forecasting models."""

from abc import ABC, abstractmethod
from typing import List, Dict, Optional
from dataclasses import dataclass
import pandas as pd


@dataclass
class ForecastResult:
    """Standardized forecast output with uncertainty quantification."""
    dates: List[str]           # ISO format dates (YYYY-MM-DD)
    yhat: List[float]          # Point predictions
    yhat_lower: List[float]    # Lower 90% CI
    yhat_upper: List[float]    # Upper 90% CI
    model_name: str            # Model identifier
    model_version: str         # Model version
    confidence: str            # "high" | "medium" | "low"
    metadata: Dict             # Additional model-specific info


class BaseForecastModel(ABC):
    """Abstract base class for all forecast models.

    Standardizes the interface for LSTM, Prophet, Ensemble, and future models.
    All concrete implementations must provide fit(), predict(), and validate().
    """

    def __init__(self, model_name: str, version: str = "1.0"):
        self.model_name = model_name
        self.version = version

    @abstractmethod
    def fit(self, df: pd.DataFrame, target_col: str, **kwargs) -> None:
        """Train the model on historical time series data.

        Args:
            df: DataFrame with 'ds' (date) column and target column
            target_col: Name of the target column to forecast
            **kwargs: Model-specific options (e.g., country for external regressors)

        Raises:
            ValueError: If insufficient data or invalid format
        """
        pass

    @abstractmethod
    def predict(self, horizon: int) -> ForecastResult:
        """Generate forecast for the specified horizon with uncertainty.

        Args:
            horizon: Number of periods (days) to forecast ahead

        Returns:
            ForecastResult with predictions and confidence intervals

        Raises:
            ValueError: If model not fitted or horizon invalid
        """
        pass

    @abstractmethod
    def validate(self, df: pd.DataFrame, target_col: str) -> Dict[str, float]:
        """Compute validation metrics on holdout set (last 20% of data).

        Args:
            df: Full dataset to split into train/test
            target_col: Target column name

        Returns:
            Dict with keys: 'mae', 'rmse', 'mape'
        """
        pass
