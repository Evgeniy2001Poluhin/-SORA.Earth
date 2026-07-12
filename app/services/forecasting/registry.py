"""Thread-safe registry for forecast models."""

from typing import Dict, Optional, Callable, List
import logging
from .base import BaseForecastModel

log = logging.getLogger(__name__)


class ModelRegistry:
    """Centralized registry for forecast model factories.

    Usage:
        # Register at module level
        ModelRegistry.register("lstm", lambda: LSTMForecaster())

        # Get instance
        model = ModelRegistry.get("lstm")
    """

    _models: Dict[str, Callable[[], BaseForecastModel]] = {}

    @classmethod
    def register(cls, name: str, factory: Callable[[], BaseForecastModel]) -> None:
        """Register a model factory by name.

        Args:
            name: Unique model identifier (e.g., "lstm", "prophet", "ensemble")
            factory: Callable that returns a fresh model instance
        """
        cls._models[name] = factory
        log.info(f"Registered forecast model: {name}")

    @classmethod
    def get(cls, name: str) -> Optional[BaseForecastModel]:
        """Lazy-load and return a fresh model instance.

        Args:
            name: Model name registered earlier

        Returns:
            New model instance or None if not found
        """
        factory = cls._models.get(name)
        if factory:
            return factory()
        log.warning(f"Model '{name}' not found in registry")
        return None

    @classmethod
    def list_models(cls) -> List[str]:
        """List all registered model names."""
        return list(cls._models.keys())
