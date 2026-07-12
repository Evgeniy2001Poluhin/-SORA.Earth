"""LSTM forecaster with MC Dropout uncertainty quantification."""

import torch
import torch.nn as nn
import numpy as np
import pandas as pd
from typing import Dict, List
from datetime import timedelta
import logging

from .base import BaseForecastModel, ForecastResult
from .features import FeatureEngineer

log = logging.getLogger(__name__)


class LSTMModel(nn.Module):
    """2-layer LSTM with Dropout for time series forecasting."""

    def __init__(self, input_size: int, hidden_size: int = 128, dropout: float = 0.2):
        super().__init__()
        self.hidden_size = hidden_size

        self.lstm1 = nn.LSTM(input_size, hidden_size, batch_first=True)
        self.dropout1 = nn.Dropout(dropout)
        self.lstm2 = nn.LSTM(hidden_size, hidden_size, batch_first=True)
        self.dropout2 = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden_size, 1)

    def forward(self, x):
        """Forward pass.

        Args:
            x: Input tensor of shape (batch, seq_len, features)

        Returns:
            Predictions of shape (batch, 1)
        """
        out, _ = self.lstm1(x)
        out = self.dropout1(out)
        out, _ = self.lstm2(out)
        out = self.dropout2(out)
        out = self.fc(out[:, -1, :])  # Take last timestep
        return out


class LSTMForecaster(BaseForecastModel):
    """LSTM forecaster with MC Dropout for uncertainty quantification.

    Architecture:
    - 2-layer LSTM with hidden_size=128
    - Dropout=0.2 for regularization and MC uncertainty
    - 50 MC samples for prediction intervals

    Uncertainty via MC Dropout: Run inference multiple times with dropout enabled,
    then compute percentiles over the ensemble of predictions.
    """

    def __init__(self, seq_length: int = 30, hidden_size: int = 128,
                 dropout: float = 0.2, mc_samples: int = 50):
        super().__init__("LSTM", "1.0")
        self.seq_length = seq_length
        self.hidden_size = hidden_size
        self.dropout = dropout
        self.mc_samples = mc_samples

        self.model = None
        self.scaler = None
        self.feature_cols = []
        self.last_date = None
        self.last_values = None

    def fit(self, df: pd.DataFrame, target_col: str, **kwargs) -> None:
        """Train LSTM on time series data.

        Args:
            df: DataFrame with 'ds' (date) and target column
            target_col: Name of target column to forecast
            **kwargs: country — ISO3 code for external regressors

        Raises:
            ValueError: If insufficient data (< seq_length + 10)
        """
        from sklearn.preprocessing import StandardScaler

        if len(df) < self.seq_length + 10:
            raise ValueError(f"Insufficient data: {len(df)} rows, need at least {self.seq_length + 10}")

        # Feature engineering
        country = kwargs.get("country")
        engineer = FeatureEngineer(lookback_window=self.seq_length)
        df_eng = engineer.engineer_all(df.copy(), target_col, country=country)

        if len(df_eng) < self.seq_length:
            raise ValueError(f"After feature engineering: {len(df_eng)} rows, need at least {self.seq_length}")

        # Identify feature columns (exclude date and target)
        self.feature_cols = [c for c in df_eng.columns if c not in ["ds", target_col]]

        # Scale features
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(df_eng[self.feature_cols])
        y = df_eng[target_col].values

        # Create sequences
        X_seq, y_seq = [], []
        for i in range(len(X_scaled) - self.seq_length):
            X_seq.append(X_scaled[i:i+self.seq_length])
            y_seq.append(y[i+self.seq_length])

        X_seq = np.array(X_seq)
        y_seq = np.array(y_seq).reshape(-1, 1)

        log.info(f"LSTM training data: {len(X_seq)} sequences, {len(self.feature_cols)} features")

        # Initialize model
        input_size = len(self.feature_cols)
        self.model = LSTMModel(input_size, hidden_size=self.hidden_size, dropout=self.dropout)

        # Training loop
        optimizer = torch.optim.Adam(self.model.parameters(), lr=0.001)
        criterion = nn.MSELoss()

        X_tensor = torch.tensor(X_seq, dtype=torch.float32)
        y_tensor = torch.tensor(y_seq, dtype=torch.float32)

        self.model.train()
        best_loss = float('inf')
        patience = 10
        patience_counter = 0

        for epoch in range(100):
            optimizer.zero_grad()
            outputs = self.model(X_tensor)
            loss = criterion(outputs, y_tensor)
            loss.backward()
            optimizer.step()

            # Simple early stopping
            if loss.item() < best_loss:
                best_loss = loss.item()
                patience_counter = 0
            else:
                patience_counter += 1

            if patience_counter >= patience:
                log.info(f"Early stopping at epoch {epoch + 1}, loss={best_loss:.6f}")
                break

        self.model.eval()

        # Store last sequence (scaled) for autoregressive prediction
        self.last_date = pd.to_datetime(df["ds"].iloc[-1])
        last_raw = df_eng[self.feature_cols].iloc[-self.seq_length:].values
        self.last_values = self.scaler.transform(last_raw)

        log.info(f"LSTM trained successfully: {epoch + 1} epochs, final loss={best_loss:.6f}")

    def predict(self, horizon: int) -> ForecastResult:
        """Generate multi-step autoregressive forecast with MC Dropout uncertainty.

        Each step feeds back into the input sequence: the predicted value replaces
        the oldest entry, and lag/rolling features are recomputed from the growing
        prediction buffer. MC Dropout samples at each step produce step-wise
        confidence intervals that naturally widen over the horizon.

        Args:
            horizon: Number of days ahead to forecast

        Returns:
            ForecastResult with point predictions and 90% confidence intervals

        Raises:
            ValueError: If model not fitted
        """
        if self.model is None:
            raise ValueError("Model not fitted. Call fit() first.")

        def enable_dropout(m):
            if isinstance(m, nn.Dropout):
                m.train()

        self.model.apply(enable_dropout)

        # mc_predictions shape: (mc_samples, horizon)
        mc_predictions = np.zeros((self.mc_samples, horizon))

        for s in range(self.mc_samples):
            # Each MC sample runs its own autoregressive chain
            seq = self.last_values.copy()  # (seq_length, n_features)

            for t in range(horizon):
                inp = torch.tensor(seq, dtype=torch.float32).unsqueeze(0)
                with torch.no_grad():
                    pred = self.model(inp).cpu().numpy().item()

                mc_predictions[s, t] = pred

                # Shift sequence: drop oldest row, append new row with updated target-derived features
                new_row = self._build_next_row(seq, pred)
                seq = np.vstack([seq[1:], new_row])

        # Aggregate across MC samples per timestep
        yhat = np.mean(mc_predictions, axis=0)
        yhat_lower = np.percentile(mc_predictions, 5, axis=0)
        yhat_upper = np.percentile(mc_predictions, 95, axis=0)

        dates = [(self.last_date + timedelta(days=i+1)).strftime("%Y-%m-%d") for i in range(horizon)]

        # Confidence from average relative CI width across the horizon
        ci_widths = yhat_upper - yhat_lower
        avg_ci = float(np.mean(ci_widths))
        avg_pred = float(np.mean(np.abs(yhat))) + 1e-10
        relative_width = avg_ci / avg_pred
        confidence = "high" if relative_width < 0.15 else "medium" if relative_width < 0.4 else "low"

        log.info(f"LSTM forecast: horizon={horizon}, confidence={confidence}, avg_CI={avg_ci:.3f}")

        return ForecastResult(
            dates=dates,
            yhat=yhat.tolist(),
            yhat_lower=yhat_lower.tolist(),
            yhat_upper=yhat_upper.tolist(),
            model_name=self.model_name,
            model_version=self.version,
            confidence=confidence,
            metadata={
                "mc_samples": self.mc_samples,
                "avg_ci_width": round(avg_ci, 4),
                "max_ci_width": round(float(np.max(ci_widths)), 4),
                "seq_length": self.seq_length,
                "autoregressive": True
            }
        )

    def _build_next_row(self, seq: np.ndarray, pred_value: float) -> np.ndarray:
        """Build the next feature row for autoregressive step.

        Approximates feature updates using the prediction value:
        - Target-lag features shift (lag1 = pred, lag7/lag30 from history)
        - Rolling stats updated with the new prediction
        - Temporal features incremented by one day
        - External regressors carried forward (last known value)
        """
        new_row = seq[-1].copy()
        n_features = len(self.feature_cols)

        # Map feature names to indices
        col_idx = {col: i for i, col in enumerate(self.feature_cols)}

        # Update lag features with predicted value
        target_prefix = None
        for col in self.feature_cols:
            if col.endswith("_lag1"):
                target_prefix = col.rsplit("_lag1", 1)[0]
                break

        if target_prefix:
            if f"{target_prefix}_lag1" in col_idx:
                new_row[col_idx[f"{target_prefix}_lag1"]] = pred_value
            if f"{target_prefix}_lag7" in col_idx and len(seq) >= 7:
                new_row[col_idx[f"{target_prefix}_lag7"]] = seq[-6, col_idx.get(f"{target_prefix}_lag1", 0)]
            if f"{target_prefix}_lag30" in col_idx and len(seq) >= 30:
                new_row[col_idx[f"{target_prefix}_lag30"]] = seq[-29, col_idx.get(f"{target_prefix}_lag1", 0)]

            # Update rolling stats from recent sequence + prediction
            recent_vals = []
            lag1_idx = col_idx.get(f"{target_prefix}_lag1", 0)
            for i in range(min(30, len(seq))):
                recent_vals.append(seq[-(i+1), lag1_idx])
            recent_vals.insert(0, pred_value)

            if f"{target_prefix}_ma7" in col_idx:
                new_row[col_idx[f"{target_prefix}_ma7"]] = np.mean(recent_vals[:7])
            if f"{target_prefix}_ma30" in col_idx:
                new_row[col_idx[f"{target_prefix}_ma30"]] = np.mean(recent_vals[:30])
            if f"{target_prefix}_std7" in col_idx:
                new_row[col_idx[f"{target_prefix}_std7"]] = np.std(recent_vals[:7]) if len(recent_vals) >= 2 else 0.0
            if f"{target_prefix}_std30" in col_idx:
                new_row[col_idx[f"{target_prefix}_std30"]] = np.std(recent_vals[:30]) if len(recent_vals) >= 2 else 0.0

        # Increment temporal features
        if "dow" in col_idx:
            new_row[col_idx["dow"]] = (new_row[col_idx["dow"]] + 1) % 7
        if "is_weekend" in col_idx:
            new_row[col_idx["is_weekend"]] = 1.0 if new_row[col_idx.get("dow", 0)] >= 5 else 0.0
        if "month" in col_idx:
            pass  # Month changes are rare enough to ignore in short horizons

        return new_row

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

            # Predict on test set (simplified - single forecast of test length)
            result = self.predict(len(test_df))
            y_pred = np.array(result.yhat)
            y_true = test_df[target_col].values[:len(y_pred)]

            mae = mean_absolute_error(y_true, y_pred)
            rmse = np.sqrt(mean_squared_error(y_true, y_pred))
            mape = np.mean(np.abs((y_true - y_pred) / (y_true + 1e-10))) * 100

            log.info(f"LSTM validation: MAE={mae:.3f}, RMSE={rmse:.3f}, MAPE={mape:.1f}%")

            return {"mae": float(mae), "rmse": float(rmse), "mape": float(mape)}
        except Exception as e:
            log.error(f"LSTM validation failed: {e}")
            return {"mae": float('inf'), "rmse": float('inf'), "mape": float('inf')}
