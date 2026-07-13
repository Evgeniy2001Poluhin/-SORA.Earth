#!/usr/bin/env python3
"""Measure Prophet MAE improvement before/after optimization.

Compares:
- Old Prophet config (conservative, MAE ~4.04)
- New Prophet config (optimized for ESG, target MAE <3.5)

Usage:
    python scripts/measure_prophet_improvement.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from sklearn.metrics import mean_absolute_error
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def generate_synthetic_esg_data(n_samples: int = 120, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic ESG score time series with realistic patterns.

    Patterns:
    - Gradual upward trend (ESG improvement over time)
    - Monthly fluctuations (reporting cycles)
    - Quarterly spikes (quarterly reports)
    - Random noise
    """
    np.random.seed(seed)

    dates = pd.date_range(
        start=datetime.now() - timedelta(days=n_samples),
        periods=n_samples,
        freq='D'
    )

    # Base trend: gradual improvement
    trend = np.linspace(45, 75, n_samples)

    # Monthly seasonality (period ~30 days)
    monthly = 3 * np.sin(2 * np.pi * np.arange(n_samples) / 30.5)

    # Quarterly spikes (period ~91 days)
    quarterly = 2 * np.sin(2 * np.pi * np.arange(n_samples) / 91.25)

    # Random noise
    noise = np.random.normal(0, 2, n_samples)

    # Combine
    scores = trend + monthly + quarterly + noise
    scores = np.clip(scores, 0, 100)  # ESG scores bounded [0, 100]

    return pd.DataFrame({
        'ds': dates,
        'esg_score': scores
    })


def train_and_evaluate_prophet(df: pd.DataFrame, config_name: str, **prophet_kwargs) -> dict:
    """Train Prophet with given config and return metrics."""
    from prophet import Prophet
    import logging as prophet_log
    prophet_log.getLogger('prophet').setLevel(prophet_log.WARNING)

    # 80/20 train/test split
    split_idx = int(len(df) * 0.8)
    train_df = df.iloc[:split_idx].copy()
    test_df = df.iloc[split_idx:].copy()

    # Prepare for Prophet
    train_prophet = train_df[['ds', 'esg_score']].copy()
    train_prophet.columns = ['ds', 'y']

    # Train
    log.info(f"\n{'='*60}")
    log.info(f"Training Prophet: {config_name}")
    log.info(f"Train samples: {len(train_prophet)}, Test samples: {len(test_df)}")

    # Extract custom seasonality flags before creating Prophet
    add_monthly = prophet_kwargs.pop('add_monthly', False)
    add_quarterly = prophet_kwargs.pop('add_quarterly', False)

    model = Prophet(**prophet_kwargs)

    # Add custom seasonality if specified
    if add_monthly:
        model.add_seasonality(
            name='monthly',
            period=30.5,
            fourier_order=3,
            prior_scale=5.0
        )

    if add_quarterly:
        model.add_seasonality(
            name='quarterly',
            period=91.25,
            fourier_order=4,
            prior_scale=3.0
        )

    model.fit(train_prophet)

    # Predict on test set
    future = pd.DataFrame({'ds': pd.to_datetime(test_df['ds'])})
    forecast = model.predict(future)

    # Metrics
    y_true = test_df['esg_score'].values
    y_pred = forecast['yhat'].values

    mae = mean_absolute_error(y_true, y_pred)
    rmse = np.sqrt(np.mean((y_true - y_pred) ** 2))
    mape = np.mean(np.abs((y_true - y_pred) / (y_true + 1e-10))) * 100

    # Confidence interval width
    ci_width = (forecast['yhat_upper'] - forecast['yhat_lower']).mean()

    log.info(f"Results:")
    log.info(f"  MAE:  {mae:.3f}")
    log.info(f"  RMSE: {rmse:.3f}")
    log.info(f"  MAPE: {mape:.1f}%")
    log.info(f"  Avg CI Width: {ci_width:.3f}")

    return {
        'config': config_name,
        'mae': mae,
        'rmse': rmse,
        'mape': mape,
        'ci_width': ci_width
    }


def main():
    """Compare old vs new Prophet configurations."""
    log.info("Generating synthetic ESG time series...")
    df = generate_synthetic_esg_data(n_samples=120)

    # Old configuration (before optimization)
    old_config = {
        'yearly_seasonality': True,
        'weekly_seasonality': True,
        'daily_seasonality': False,
        'changepoint_prior_scale': 0.05,  # Conservative
        'uncertainty_samples': 1000,
        'interval_width': 0.8
    }

    # New configuration (optimized for ESG)
    n_samples = len(df)
    new_config = {
        'yearly_seasonality': False if n_samples < 60 else True,
        'weekly_seasonality': True if n_samples >= 14 else False,
        'daily_seasonality': False,
        'changepoint_prior_scale': 0.1,  # More sensitive
        'changepoint_range': 0.9,
        'uncertainty_samples': 1000,
        'interval_width': 0.8,
        'growth': 'linear',
        'seasonality_mode': 'additive',
        'seasonality_prior_scale': 10.0,
        'add_monthly': True,  # Custom flag for add_seasonality
        'add_quarterly': True
    }

    # Evaluate both
    results_old = train_and_evaluate_prophet(df, "Old Config (Conservative)", **old_config)
    results_new = train_and_evaluate_prophet(df, "New Config (ESG-Optimized)", **new_config)

    # Summary
    log.info(f"\n{'='*60}")
    log.info("SUMMARY")
    log.info(f"{'='*60}")
    log.info(f"{'Metric':<15} {'Old':<12} {'New':<12} {'Improvement':<15}")
    log.info(f"{'-'*60}")

    mae_improvement = ((results_old['mae'] - results_new['mae']) / results_old['mae']) * 100
    rmse_improvement = ((results_old['rmse'] - results_new['rmse']) / results_old['rmse']) * 100

    log.info(f"{'MAE':<15} {results_old['mae']:<12.3f} {results_new['mae']:<12.3f} {mae_improvement:>+.1f}%")
    log.info(f"{'RMSE':<15} {results_old['rmse']:<12.3f} {results_new['rmse']:<12.3f} {rmse_improvement:>+.1f}%")
    log.info(f"{'MAPE':<15} {results_old['mape']:<12.1f} {results_new['mape']:<12.1f}")
    log.info(f"{'CI Width':<15} {results_old['ci_width']:<12.3f} {results_new['ci_width']:<12.3f}")

    log.info(f"\n{'='*60}")

    # Check if target achieved
    target_mae = 3.5
    if results_new['mae'] < target_mae:
        log.info(f"✅ TARGET ACHIEVED: New MAE ({results_new['mae']:.3f}) < {target_mae}")
    else:
        log.info(f"⚠️  Target not met: New MAE ({results_new['mae']:.3f}) >= {target_mae}")
        log.info(f"   Improvement still: {mae_improvement:+.1f}%")

    return results_old, results_new


if __name__ == "__main__":
    main()
