"""Production-grade time series forecasting module.

Architecture:
- BaseForecastModel: Abstract interface for all models
- ModelRegistry: Thread-safe factory registry
- LSTMForecaster: Primary model with MC Dropout uncertainty
- ProphetForecaster: Seasonal decomposition fallback
- EnsembleForecaster: Auto-weighted combination
- FeatureEngineer: Time series feature engineering pipeline

Fallback chain: Ensemble → Prophet → LinearTrend (never fails)
"""

from .base import BaseForecastModel, ForecastResult
from .registry import ModelRegistry
from .cache import ForecastModelCache, forecast_cache

__all__ = [
    "BaseForecastModel",
    "ForecastResult",
    "ModelRegistry",
    "ForecastModelCache",
    "forecast_cache",
]

# Lazy imports to avoid circular dependencies and heavy loading
def _get_lstm():
    from .lstm import LSTMForecaster
    return LSTMForecaster()

def _get_prophet():
    from .prophet import ProphetForecaster
    return ProphetForecaster()

def _get_ensemble():
    from .ensemble import EnsembleForecaster
    return EnsembleForecaster()

def _get_linear():
    from .linear import LinearTrendForecaster
    return LinearTrendForecaster()

def _get_production(metric="score"):
    from .production_forecaster import ProductionForecaster
    return ProductionForecaster(metric=metric)

# Register models
ModelRegistry.register("lstm", _get_lstm)
ModelRegistry.register("prophet", _get_prophet)
ModelRegistry.register("ensemble", _get_ensemble)
ModelRegistry.register("linear", _get_linear)
ModelRegistry.register("production", _get_production)
