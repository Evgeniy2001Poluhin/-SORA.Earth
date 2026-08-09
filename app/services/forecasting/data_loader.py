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
    """Load evaluation history as a gap-free daily series.

    Pipeline, in order:

    1. evaluations sharing a date are aggregated into a daily mean;
    2. the series is reindexed onto every calendar day between the first and
       last observation, and interior gaps are filled by linear interpolation;
    3. a centred 3-day rolling mean is applied to the result.

    No row is produced outside the observed range: there is no backward or
    forward extension, and nothing is emitted before the first or after the last
    observation.

    Limits the caller has to account for:

    * **No provenance.** The frame is `ds` and `y` only. Nothing marks which
      days were observed and which were interpolated, so no consumer can tell
      them apart.
    * **Smoothing rewrites observed values too.** Step 3 applies to the whole
      column, so a returned value at an observed date is generally not the
      measurement taken that day.
    * **`len()` is a calendar span, not a count of observations.** Interpolation
      is what makes it large. A long, sparsely observed range and a short, fully
      observed one can return the same number of rows, so the length of this
      frame is not evidence that the data are sufficient for anything.

    Readiness is not decided here. This loader applies no threshold of its own;
    whether a series supports a given model is ensemble policy, in
    `app/services/forecasting/ensemble.py`.

    Args:
        db: SQLAlchemy database session
        metric: Target metric name (score/prob/co2_reduction)

    Returns:
        DataFrame with columns ["ds", "y"] -- one row per calendar day from the
        first to the last observation, with no flag for which rows were observed.
        Empty (same columns) when there are no rows or no values for the metric.
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

    # Naming the columns here is what keeps the empty result usable: `recs` is
    # already filtered by `is not None`, so rows-present/all-NULL arrives as an
    # empty list, and a DataFrame built from one has no columns at all. This
    # frame is returned as-is below, so the schema set here is the schema the
    # caller sees.
    df = pd.DataFrame(recs, columns=["ds", "y"]).dropna()
    if df.empty:
        return df

    # Aggregate to daily mean
    df = df.set_index("ds").resample("D")["y"].mean().reset_index()

    # Fill ALL date gaps with interpolation (creates continuous time series)
    date_range = pd.date_range(start=df["ds"].min(), end=df["ds"].max(), freq="D")
    df = df.set_index("ds").reindex(date_range).interpolate(method="linear").reset_index()
    df.columns = ["ds", "y"]

    # REMOVED: Synthetic extension with random noise pollutes training
    # Instead: Lower LSTM threshold to work with real interpolated data
    # Original issue: np.random.normal() creates non-deterministic data
    # → Different model every restart, metrics meaningless, forecasts unstable

    # Light smoothing to reduce noise
    df["y"] = df["y"].rolling(3, min_periods=1, center=True).mean()

    log.info(f"Time series for {metric}: {len(df)} days (final)")
    return df
