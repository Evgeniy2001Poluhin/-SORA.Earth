"""Model caching layer to avoid retraining on every request."""

import hashlib
import time
import threading
import logging
from typing import Optional
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .base import BaseForecastModel, ForecastResult

log = logging.getLogger(__name__)

DEFAULT_TTL = 300  # 5 minutes


@dataclass
class CacheEntry:
    model: BaseForecastModel
    data_hash: str
    fitted_at: float
    ttl: float


class ForecastModelCache:
    """Thread-safe LRU cache for fitted forecast models.

    Avoids retraining when the same data is requested within the TTL window.
    Cache key = (model_name, metric, data_hash). If data changes, cache misses
    and the model is retrained.
    """

    def __init__(self, max_entries: int = 16, default_ttl: float = DEFAULT_TTL):
        self._cache: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self._default_ttl = default_ttl

    @staticmethod
    def _compute_data_hash(df: pd.DataFrame) -> str:
        """Fast hash of DataFrame length + numeric tail for change detection."""
        n = len(df)
        numeric_cols = df.select_dtypes(include=[np.number])
        if numeric_cols.empty:
            tail_bytes = str(n).encode()
        else:
            tail = numeric_cols.tail(5) if n >= 5 else numeric_cols
            tail_bytes = tail.to_numpy().tobytes()
        h = hashlib.md5(usedforsecurity=False)
        h.update(f"{n}:{list(df.columns)}".encode())
        h.update(tail_bytes)
        return h.hexdigest()[:12]

    def _cache_key(self, model_name: str, metric: str, data_hash: str) -> str:
        return f"{model_name}:{metric}:{data_hash}"

    def get_fitted_model(
        self, model_name: str, metric: str, df: pd.DataFrame
    ) -> Optional[BaseForecastModel]:
        """Return cached fitted model if available and not expired."""
        data_hash = self._compute_data_hash(df)
        key = self._cache_key(model_name, metric, data_hash)

        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if time.time() - entry.fitted_at > entry.ttl:
                del self._cache[key]
                log.debug(f"Cache expired: {key}")
                return None
            log.info(f"Cache hit: {key}")
            return entry.model

    def store_fitted_model(
        self, model_name: str, metric: str, df: pd.DataFrame,
        model: BaseForecastModel, ttl: Optional[float] = None
    ) -> None:
        """Store a fitted model in cache."""
        data_hash = self._compute_data_hash(df)
        key = self._cache_key(model_name, metric, data_hash)

        with self._lock:
            if len(self._cache) >= self._max_entries:
                oldest_key = min(self._cache, key=lambda k: self._cache[k].fitted_at)
                del self._cache[oldest_key]
                log.debug(f"Cache evicted: {oldest_key}")

            self._cache[key] = CacheEntry(
                model=model,
                data_hash=data_hash,
                fitted_at=time.time(),
                ttl=ttl or self._default_ttl
            )
            log.info(f"Cache stored: {key}")

    def invalidate(self, model_name: Optional[str] = None) -> int:
        """Clear cache entries. If model_name given, only that model's entries."""
        with self._lock:
            if model_name is None:
                count = len(self._cache)
                self._cache.clear()
            else:
                keys_to_remove = [k for k in self._cache if k.startswith(f"{model_name}:")]
                for k in keys_to_remove:
                    del self._cache[k]
                count = len(keys_to_remove)
        log.info(f"Cache invalidated: {count} entries removed")
        return count

    @property
    def size(self) -> int:
        return len(self._cache)


# Global singleton
forecast_cache = ForecastModelCache()
