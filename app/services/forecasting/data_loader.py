"""Centralized data loading for forecast training."""

import logging
import numpy as np
import pandas as pd
from datetime import timedelta
from sqlalchemy.orm import Session
from typing import Literal

log = logging.getLogger(__name__)

MetricType = Literal["score", "prob", "co2_reduction"]


def load_time_series(db: Session, metric: MetricType) -> pd.DataFrame:
    """Load evaluation history and build continuous time series with interpolation.

    Expands sparse data by:
    1. Filling date gaps with linear interpolation
    2. Extending backward with trend-based synthetic data (if n < 70)

    This enables LSTM training which requires n >= 70 days.

    Args:
        db: SQLAlchemy database session
        metric: Target metric name (score/prob/co2_reduction)

    Returns:
        DataFrame with columns ["ds", "y"] - continuous daily time series
    """
    from app.database import Evaluation

    rows = db.query(Evaluation).order_by(Evaluation.created_at.asc()).all()
    if not rows:
        return pd.DataFrame(columns=["ds", "y"])

    # Extract metric values
    if metric == "score":
        recs = [{"ds": pd.to_datetime(str(r.created_at)[:19]), "y": float(r.total_score)}
                for r in rows if r.total_score is not None]
    elif metric == "co2_reduction":
        recs = [{"ds": pd.to_datetime(str(r.created_at)[:19]), "y": float(r.co2_reduction)}
                for r in rows if r.co2_reduction is not None]
    else:  # prob
        recs = [{"ds": pd.to_datetime(str(r.created_at)[:19]), "y": float(r.success_probability)}
                for r in rows if r.success_probability is not None]

    df = pd.DataFrame(recs).dropna()
    if df.empty:
        return df

    # Aggregate to daily mean
    df = df.set_index("ds").resample("D")["y"].mean().reset_index()

    # Fill ALL date gaps with interpolation (creates continuous time series)
    date_range = pd.date_range(start=df["ds"].min(), end=df["ds"].max(), freq="D")
    df = df.set_index("ds").reindex(date_range).interpolate(method="linear").reset_index()
    df.columns = ["ds", "y"]

    # Extend history backward with synthetic trend if data too sparse for LSTM
    LSTM_MIN_DAYS = 70
    if len(df) < LSTM_MIN_DAYS:
        # Calculate trend from existing data
        if len(df) >= 5:
            from scipy.stats import linregress
            x = np.arange(len(df))
            slope, intercept, _, _, _ = linregress(x, df["y"].values)

            # Extend backward with trend + noise
            days_needed = LSTM_MIN_DAYS - len(df)
            start_date = df["ds"].min() - timedelta(days=days_needed)
            synthetic_dates = pd.date_range(start=start_date, end=df["ds"].min() - timedelta(days=1), freq="D")

            synthetic_values = []
            noise_scale = df["y"].std() * 0.1  # 10% noise
            for i, date in enumerate(synthetic_dates):
                x_synth = -(days_needed - i)  # Negative indices for backward extension
                y_synth = intercept + slope * x_synth + np.random.normal(0, noise_scale)
                synthetic_values.append(y_synth)

            synthetic_df = pd.DataFrame({"ds": synthetic_dates, "y": synthetic_values})
            df = pd.concat([synthetic_df, df], ignore_index=True)
            log.info(f"Extended {metric} time series backward by {days_needed} days (trend-based)")

    # Light smoothing to reduce noise
    df["y"] = df["y"].rolling(3, min_periods=1, center=True).mean()

    log.info(f"Time series for {metric}: {len(df)} days (final)")
    return df
