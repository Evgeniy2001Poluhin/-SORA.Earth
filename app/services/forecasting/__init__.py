"""Production-grade time series forecasting module.

Architecture:
- BaseForecastModel: Abstract interface for all models
- ModelRegistry: Thread-safe factory registry
- LSTMForecaster: MC Dropout uncertainty; an ensemble member
- ProphetForecaster: Seasonal decomposition; an ensemble member
- LinearTrendForecaster: An ensemble member, weighted only in the small tiers
- EnsembleForecaster: Sample-size-weighted combination of the three
- FeatureEngineer: Time series feature engineering pipeline

There are two distinct mechanisms here, and this docstring used to describe a
third that does not exist -- "Fallback chain: Ensemble -> Prophet ->
LinearTrend (never fails)". No such chain is implemented (#123).

**Production degradation** (`production_forecaster.py`) has two stages: a
stored MLflow model, and, if none is available or it fails to load, an
`EnsembleForecaster` trained on demand. Prophet and LinearTrend are not stages
of it; they exist only inside the ensemble.

**Ensemble composition** (`ensemble.py`) is not degradation at all. All three
members are weighted by sample size, and the weights are chosen at `fit()`
time -- see that module's docstring for the tier table. LinearTrend carries
weight only in the two smallest tiers and is 0.0 in the rest; it is reached by
being weighted in, never by Prophet failing.

Two consequences worth stating rather than leaving to be discovered:

- **A member failing redistributes weight silently.** A failed LSTM fit hands
  its weight to Prophet and a failed Prophet fit hands its to LSTM, logged and
  otherwise unremarked. Two calls that both report "the ensemble" need not
  have run the same combination.
- **"Always returns a number" is not "never fails".** The stack is built so a
  caller gets a result; that guarantee is about the return value, not about
  the model behind it, and it says nothing about which members contributed.

This is why `docs/M2_EVALUATION_PROTOCOL.md` §8.1 pins `ProphetForecaster`
directly for evaluation rather than using this stack, and requires any
fallback to be recorded per fold.
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
