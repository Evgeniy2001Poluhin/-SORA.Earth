"""Ensemble forecaster with auto-weighting and cold-start handling."""

import logging
import numpy as np
import pandas as pd
from typing import Dict, Optional

from .base import BaseForecastModel, ForecastResult
from .lstm import LSTMForecaster
from .prophet import ProphetForecaster
from .linear import LinearTrendForecaster

log = logging.getLogger(__name__)

LSTM_MIN_ROWS = 35  # MUST match lstm.py MIN_ROWS = seq_length(30) + 5
# Critical: This threshold must align with LSTMForecaster.fit() internal check
# to avoid "LSTM fit failed" exceptions in production
# With current data (~28 days after interpolation), LSTM will be skipped


class EnsembleForecaster(BaseForecastModel):
    """Ensemble model with intelligent auto-weighting and cold-start strategy.

    Weighting strategy:
    - n >= 100: LSTM=0.7, Prophet=0.3
    - 70 <= n < 100: LSTM=0.3, Prophet=0.7
    - n < 70: Skip LSTM entirely (insufficient for feature engineering)
    - n < 10: Use LinearTrend fallback with bootstrap augmentation for Prophet

    Fallback chain: LSTM + Prophet → Prophet-only → LinearTrend
    """

    def __init__(self):
        super().__init__("Ensemble", "1.0")
        self.lstm = LSTMForecaster(seq_length=30, hidden_size=128, dropout=0.2, mc_samples=50)
        self.prophet = ProphetForecaster()
        self.linear = LinearTrendForecaster()
        self.weights = {"lstm": 0.5, "prophet": 0.5}  # Initial default
        self._active_models: list[tuple[str, BaseForecastModel]] = []

    def fit(self, df: pd.DataFrame, target_col: str, **kwargs) -> None:
        """Fit models with cold-start aware strategy.

        Args:
            df: DataFrame with 'ds' (date) and target column
            target_col: Target column name
            **kwargs: Passed to sub-models (e.g., country for LSTM regressors)

        Raises:
            RuntimeError: If all models fail
        """
        n_samples = len(df)
        self._active_models = []

        # Cold-start: very small data — augment for Prophet, always include LinearTrend
        if n_samples < 10:
            log.info(f"Cold-start mode (n={n_samples}): using LinearTrend + bootstrap Prophet")
            self.weights = {"lstm": 0.0, "prophet": 0.6, "linear": 0.4}
            df_aug = self._bootstrap_augment(df, target_col, target_size=30)
            try:
                self.prophet.fit(df_aug, target_col, **kwargs)
                self._active_models.append(("prophet", self.prophet))
            except Exception as e:
                log.warning(f"Prophet failed on augmented data: {e}")
                self.weights["prophet"] = 0.0
                self.weights["linear"] = 1.0
                self._normalize_weights()

            try:
                self.linear.fit(df, target_col)
                self._active_models.append(("linear", self.linear))
            except Exception as e:
                log.warning(f"LinearTrend failed: {e}")
                self.weights["linear"] = 0.0
                self._normalize_weights()

            if not self._active_models:
                raise RuntimeError("All models failed to fit (cold-start)")
            self._normalize_weights()
            return

        # Normal mode: decide whether to include LSTM
        skip_lstm = n_samples < LSTM_MIN_ROWS

        if n_samples >= 100:
            self.weights = {"lstm": 0.7, "prophet": 0.3, "linear": 0.0}
            log.info(f"Auto-weights (n={n_samples}): LSTM=0.7, Prophet=0.3")
        elif n_samples >= 50 and not skip_lstm:
            self.weights = {"lstm": 0.4, "prophet": 0.6, "linear": 0.0}
            log.info(f"Auto-weights (n={n_samples}): LSTM=0.4, Prophet=0.6 (moderate sample)")
        elif not skip_lstm:
            self.weights = {"lstm": 0.2, "prophet": 0.8, "linear": 0.0}
            log.info(f"Auto-weights (n={n_samples}): LSTM=0.2, Prophet=0.8 (small sample, conservative)")
        else:
            self.weights = {"lstm": 0.0, "prophet": 0.9, "linear": 0.1}
            log.info(f"Auto-weights (n={n_samples}): skipping LSTM (need >={LSTM_MIN_ROWS}), Prophet=0.9")

        # Fit LSTM if viable
        if not skip_lstm:
            try:
                log.info("Fitting LSTM...")
                self.lstm.fit(df, target_col, **kwargs)
                self._active_models.append(("lstm", self.lstm))
                log.info("LSTM fitted successfully")
            except Exception as e:
                log.warning(f"LSTM fit failed: {e}. Redistributing weight to Prophet")
                self.weights["lstm"] = 0.0
                # Don't let Prophet reach 1.0 - keep Linear as safety net
                self.weights["prophet"] = min(0.9, self.weights["prophet"] + 0.7)
                self._normalize_weights()

        # Fit Prophet
        try:
            log.info("Fitting Prophet...")
            self.prophet.fit(df, target_col, **kwargs)
            self._active_models.append(("prophet", self.prophet))
            log.info("Prophet fitted successfully")
        except Exception as e:
            log.warning(f"Prophet fit failed: {e}. Redistributing weight")
            self.weights["prophet"] = 0.0
            if self._active_models:
                self.weights["lstm"] = 1.0
            else:
                self.weights["linear"] = 1.0
            self._normalize_weights()

        # LinearTrend as safety net
        try:
            self.linear.fit(df, target_col)
            self._active_models.append(("linear", self.linear))
        except Exception:
            pass

        if not self._active_models:
            raise RuntimeError("All models failed to fit")

        self._normalize_weights()

    @staticmethod
    def _bootstrap_augment(df: pd.DataFrame, target_col: str, target_size: int) -> pd.DataFrame:
        """Augment small dataset by inserting jittered interpolations between existing points.

        Instead of appending future dates (which misleads Prophet's trend),
        we insert intermediate dates between existing observations with
        interpolated values + small noise. This preserves the real date range
        and doesn't introduce false trends.
        """
        if len(df) >= target_size:
            return df

        y_values = df[target_col].values
        dates = pd.to_datetime(df["ds"])
        noise_scale = np.std(y_values) * 0.05 if len(y_values) > 1 else 0.5

        # Insert points between each consecutive pair
        new_rows = []
        n_needed = target_size - len(df)
        pairs = list(zip(dates[:-1], dates[1:], y_values[:-1], y_values[1:]))

        i = 0
        while len(new_rows) < n_needed and pairs:
            d1, d2, y1, y2 = pairs[i % len(pairs)]
            gap_days = (d2 - d1).days
            if gap_days > 1:
                frac = np.random.uniform(0.3, 0.7)
                new_date = d1 + pd.Timedelta(days=int(gap_days * frac))
                new_y = y1 + frac * (y2 - y1) + np.random.normal(0, noise_scale)
                new_rows.append({"ds": new_date, target_col: new_y})
            i += 1
            if i >= len(pairs) * 3:
                break

        # If still not enough (dates too close), duplicate with jitter
        while len(new_rows) < n_needed:
            idx = np.random.randint(0, len(df))
            row_date = dates.iloc[idx]
            row_y = y_values[idx] + np.random.normal(0, noise_scale)
            offset_hours = np.random.randint(1, 12)
            new_rows.append({"ds": row_date + pd.Timedelta(hours=offset_hours), target_col: row_y})

        extra_df = pd.DataFrame(new_rows)
        augmented = pd.concat([df[["ds", target_col]], extra_df], ignore_index=True)
        augmented = augmented.sort_values("ds").reset_index(drop=True)
        log.info(f"Bootstrap augmentation: {len(df)} → {len(augmented)} rows (in-range jitter)")
        return augmented

    def _normalize_weights(self) -> None:
        """Normalize weights to sum to 1.0, zeroing out inactive models."""
        total = sum(max(0, w) for w in self.weights.values())
        if total > 0:
            self.weights = {k: max(0, v) / total for k, v in self.weights.items()}
        log.debug(f"Normalized weights: {self.weights}")

    def predict(self, horizon: int) -> ForecastResult:
        """Generate weighted ensemble prediction from active models.

        Args:
            horizon: Number of days ahead to forecast

        Returns:
            ForecastResult with ensemble predictions

        Raises:
            RuntimeError: If all models fail
        """
        predictions = []

        for name, model in self._active_models:
            weight = self.weights.get(name, 0.0)
            if weight <= 0:
                continue
            try:
                forecast = model.predict(horizon)
                predictions.append((weight, forecast))
                log.debug(f"{name} predict success, weight={weight}")
            except Exception as e:
                log.warning(f"{name} predict failed: {e}")

        if not predictions:
            raise RuntimeError("All ensemble models failed during prediction")

        # Weighted average of predictions
        total_weight = sum(w for w, _ in predictions)
        yhat = sum(w * np.array(f.yhat) for w, f in predictions) / total_weight
        yhat_lower = sum(w * np.array(f.yhat_lower) for w, f in predictions) / total_weight
        yhat_upper = sum(w * np.array(f.yhat_upper) for w, f in predictions) / total_weight

        # Use dates from first available forecast
        dates = predictions[0][1].dates

        # Weighted confidence score
        confidence_map = {"high": 3, "medium": 2, "low": 1}
        conf_score = sum(w * confidence_map[f.confidence] for w, f in predictions) / total_weight
        confidence = "high" if conf_score >= 2.5 else "medium" if conf_score >= 1.5 else "low"

        models_used = [f.model_name for _, f in predictions]
        log.info(f"Ensemble prediction: models={models_used}, confidence={confidence}")

        return ForecastResult(
            dates=dates,
            yhat=yhat.tolist(),
            yhat_lower=yhat_lower.tolist(),
            yhat_upper=yhat_upper.tolist(),
            model_name="Ensemble",
            model_version="1.0",
            confidence=confidence,
            metadata={
                "weights": self.weights,
                "models_used": models_used,
                "total_weight": float(total_weight)
            }
        )

    def validate(self, df: pd.DataFrame, target_col: str) -> Dict[str, float]:
        """Validate ensemble on holdout set with dynamic weighting.

        Runs validation on active models and adjusts weights based on performance.

        Args:
            df: Full dataset
            target_col: Target column name

        Returns:
            Dict with weighted ensemble metrics (MAE, RMSE, MAPE)
        """
        # First fit to determine active models
        if not self._active_models:
            self.fit(df, target_col)

        model_metrics: dict[str, dict[str, float]] = {}

        for name, model in self._active_models:
            try:
                metrics = model.validate(df, target_col)
                model_metrics[name] = metrics
                log.info(f"{name} validation: MAE={metrics['mae']:.3f}")
            except Exception as e:
                log.warning(f"{name} validation failed: {e}")
                self.weights[name] = 0.0

        if not model_metrics:
            return {"mae": 0.0, "rmse": 0.0, "mape": 0.0}

        # Auto-adjust weights: best model gets 0.8, rest split 0.2
        if len(model_metrics) > 1:
            best_name = min(model_metrics, key=lambda n: model_metrics[n]["mae"])
            for name in self.weights:
                if name == best_name:
                    self.weights[name] = 0.8
                elif name in model_metrics:
                    self.weights[name] = 0.2 / (len(model_metrics) - 1)
                else:
                    self.weights[name] = 0.0
            self._normalize_weights()
            log.info(f"{best_name} wins validation (MAE={model_metrics[best_name]['mae']:.3f})")

        # Compute weighted ensemble metrics
        total_weight = sum(self.weights.get(n, 0) for n in model_metrics)
        if total_weight <= 0:
            return next(iter(model_metrics.values()))

        ensemble_metrics = {"mae": 0.0, "rmse": 0.0, "mape": 0.0}
        for name, metrics in model_metrics.items():
            w = self.weights.get(name, 0) / total_weight
            for key in ensemble_metrics:
                ensemble_metrics[key] += w * metrics[key]

        log.info(f"Ensemble validation: MAE={ensemble_metrics['mae']:.3f}, "
                 f"RMSE={ensemble_metrics['rmse']:.3f}, MAPE={ensemble_metrics['mape']:.1f}%")

        return ensemble_metrics
