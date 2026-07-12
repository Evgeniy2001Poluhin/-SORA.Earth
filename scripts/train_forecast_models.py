#!/usr/bin/env python3
"""Train and register LSTM, Prophet, and Ensemble forecast models to MLflow.

Usage:
    python scripts/train_forecast_models.py

This script:
1. Loads historical evaluation data from database
2. Trains models on multiple metrics (score, prob, co2_reduction)
3. Logs models and metrics to MLflow with versioning
4. Registers models in MLflow Model Registry
5. Verifies model artifacts can be loaded back for inference
"""

import os
import sys
import pandas as pd
import numpy as np
import logging
import pickle
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal, Evaluation
from app.services.forecasting.lstm import LSTMForecaster
from app.services.forecasting.prophet import ProphetForecaster
from app.services.forecasting.ensemble import EnsembleForecaster

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)


METRICS = {
    "score": ("total_score", "ESG Total Score"),
    "prob": ("success_probability", "Success Probability"),
    "co2": ("co2_reduction", "CO2 Reduction")
}


def load_evaluation_history(metric_field: str):
    """Load evaluation history from database for specific metric."""
    db = SessionLocal()
    try:
        rows = db.query(Evaluation).order_by(Evaluation.created_at.asc()).all()

        if not rows:
            raise ValueError("No evaluation data found in database")

        data = []
        for r in rows:
            value = getattr(r, metric_field, None)
            if value is not None:
                data.append({
                    "ds": pd.to_datetime(str(r.created_at)[:19]),
                    "y": float(value)
                })

        df = pd.DataFrame(data).dropna()

        if df.empty:
            log.warning(f"No data for metric {metric_field}")
            return df

        # Aggregate to daily mean
        df = df.set_index("ds").resample("D")["y"].mean().dropna().reset_index()
        df["y"] = df["y"].rolling(7, min_periods=1, center=True).mean()

        log.info(f"Loaded {len(df)} days of evaluation history for {metric_field}")
        return df

    finally:
        db.close()


def train_and_log_model(model_class, model_name, df, target_col, mlflow, **model_kwargs):
    """Train a model and log to MLflow with metrics."""
    log.info(f"Training {model_name}...")

    model = model_class(**model_kwargs) if model_kwargs else model_class()

    try:
        # Validate (includes fit + predict on holdout)
        metrics = model.validate(df, target_col)
        log.info(f"{model_name} validation - MAE: {metrics['mae']:.3f}, "
                 f"RMSE: {metrics['rmse']:.3f}, MAPE: {metrics['mape']:.1f}%")

        # Fit on full data for production use
        model.fit(df, target_col)

        return model, metrics

    except Exception as e:
        log.error(f"{model_name} training failed: {e}")
        return None, {"mae": float('inf'), "rmse": float('inf'), "mape": float('inf')}


def verify_model_load(model_uri, mlflow):
    """Verify that a model can be loaded from MLflow."""
    try:
        # Try loading as PyTorch
        try:
            loaded = mlflow.pytorch.load_model(model_uri)
            log.info(f"✓ Model loaded successfully as PyTorch from {model_uri}")
            return True
        except Exception:
            pass

        # Try loading as generic artifact
        try:
            loaded = mlflow.pyfunc.load_model(model_uri)
            log.info(f"✓ Model loaded successfully as pyfunc from {model_uri}")
            return True
        except Exception:
            pass

        log.warning(f"✗ Could not load model from {model_uri}")
        return False

    except Exception as e:
        log.error(f"✗ Model load verification failed: {e}")
        return False


def main():
    """Main training pipeline with MLflow tracking and model registry."""
    try:
        import mlflow
        import mlflow.pytorch
        import mlflow.pyfunc

        # Configure MLflow
        mlflow_uri = os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5556")
        mlflow.set_tracking_uri(mlflow_uri)
        mlflow.set_experiment("time-series-forecasting")

        log.info(f"MLflow tracking URI: {mlflow_uri}")
        log.info("=" * 80)

        results = {}

        for metric_key, (metric_field, metric_display) in METRICS.items():
            log.info(f"\n{'=' * 80}")
            log.info(f"Training models for metric: {metric_display} ({metric_field})")
            log.info(f"{'=' * 80}\n")

            # Load data for this metric
            df = load_evaluation_history(metric_field)

            if len(df) < 30:
                log.warning(f"Insufficient data for {metric_field}: {len(df)} days, need at least 30")
                continue

            metric_results = {}

            # 1. Train LSTM
            with mlflow.start_run(run_name=f"lstm-{metric_key}-v1") as run:
                lstm, lstm_metrics = train_and_log_model(
                    LSTMForecaster,
                    "LSTM",
                    df,
                    target_col="y",
                    mlflow=mlflow,
                    seq_length=30,
                    hidden_size=128,
                    dropout=0.2,
                    mc_samples=50
                )

                if lstm and lstm_metrics['mae'] != float('inf'):
                    # Log parameters
                    mlflow.log_params({
                        "model_type": "LSTM",
                        "metric": metric_field,
                        "seq_length": lstm.seq_length,
                        "hidden_size": lstm.hidden_size,
                        "dropout": lstm.dropout,
                        "mc_samples": lstm.mc_samples,
                        "n_samples": len(df)
                    })

                    # Log metrics
                    mlflow.log_metrics(lstm_metrics)

                    # Log model (PyTorch)
                    mlflow.pytorch.log_model(lstm.model, "lstm_model")

                    # Register model in Model Registry
                    model_name = f"lstm-forecaster-{metric_key}"
                    model_uri = f"runs:/{run.info.run_id}/lstm_model"
                    mlflow.register_model(model_uri, model_name)

                    log.info(f"✓ LSTM model registered: {model_name}")

                    # Verify model can be loaded
                    verify_model_load(model_uri, mlflow)

                    metric_results["lstm"] = lstm_metrics

            # 2. Train Prophet
            with mlflow.start_run(run_name=f"prophet-{metric_key}-v1") as run:
                prophet, prophet_metrics = train_and_log_model(
                    ProphetForecaster,
                    "Prophet",
                    df,
                    target_col="y",
                    mlflow=mlflow
                )

                if prophet and prophet_metrics['mae'] != float('inf'):
                    # Log parameters
                    mlflow.log_params({
                        "model_type": "Prophet",
                        "metric": metric_field,
                        "yearly_seasonality": True,
                        "weekly_seasonality": True,
                        "n_samples": len(df)
                    })

                    # Log metrics
                    mlflow.log_metrics(prophet_metrics)

                    # Save Prophet model as pickle
                    with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
                        pickle.dump(prophet.model, f)
                        prophet_path = f.name

                    mlflow.log_artifact(prophet_path, "model")
                    os.unlink(prophet_path)

                    log.info(f"✓ Prophet model logged to MLflow")

                    metric_results["prophet"] = prophet_metrics

            # 3. Train Ensemble
            with mlflow.start_run(run_name=f"ensemble-{metric_key}-v1") as run:
                ensemble, ensemble_metrics = train_and_log_model(
                    EnsembleForecaster,
                    "Ensemble",
                    df,
                    target_col="y",
                    mlflow=mlflow
                )

                if ensemble and ensemble_metrics['mae'] != float('inf'):
                    # Log parameters
                    mlflow.log_params({
                        "model_type": "Ensemble",
                        "metric": metric_field,
                        "weights": str(ensemble.weights),
                        "active_models": str([name for name, _ in ensemble._active_models]),
                        "n_samples": len(df)
                    })

                    # Log metrics
                    mlflow.log_metrics(ensemble_metrics)

                    # Save ensemble state
                    ensemble_state = {
                        "weights": ensemble.weights,
                        "active_models": [name for name, _ in ensemble._active_models],
                        "lstm_fitted": ensemble.lstm.model is not None if hasattr(ensemble, 'lstm') else False
                    }

                    with tempfile.NamedTemporaryFile(mode='w', suffix=".json", delete=False) as f:
                        import json
                        json.dump(ensemble_state, f)
                        state_path = f.name

                    mlflow.log_artifact(state_path, "model")
                    os.unlink(state_path)

                    log.info(f"✓ Ensemble model logged to MLflow")

                    metric_results["ensemble"] = ensemble_metrics

            results[metric_key] = metric_results

        # Print summary
        log.info(f"\n{'=' * 80}")
        log.info("TRAINING SUMMARY")
        log.info(f"{'=' * 80}\n")

        for metric_key, metric_results in results.items():
            if not metric_results:
                continue

            log.info(f"{METRICS[metric_key][1]}:")
            for model_name, metrics in metric_results.items():
                log.info(f"  {model_name:10s} - MAE: {metrics['mae']:6.3f}, "
                        f"RMSE: {metrics['rmse']:6.3f}, MAPE: {metrics['mape']:5.1f}%")

        log.info(f"\n✓ All models trained and logged successfully!")
        log.info(f"📊 View results at: {mlflow_uri}/#/experiments")

    except ImportError as e:
        log.error(f"MLflow not available: {e}")
        log.info("Training models without MLflow logging...")

        # Train without MLflow
        for metric_key, (metric_field, metric_display) in METRICS.items():
            df = load_evaluation_history(metric_field)
            if len(df) < 30:
                continue

            log.info(f"\nTraining models for {metric_display}...")
            lstm, lstm_metrics = train_and_log_model(LSTMForecaster, "LSTM", df, "y", None,
                                                     seq_length=30, hidden_size=128, dropout=0.2, mc_samples=50)
            prophet, prophet_metrics = train_and_log_model(ProphetForecaster, "Prophet", df, "y", None)
            ensemble, ensemble_metrics = train_and_log_model(EnsembleForecaster, "Ensemble", df, "y", None)

        log.info("Models trained successfully (no MLflow logging)")

    except Exception as e:
        log.error(f"Training pipeline failed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
