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

    1. every `Evaluation` row carrying the metric, resampled to a **daily mean**
       -- several evaluations on one day collapse into one point;
    2. reindexed onto every calendar day from the first to the last observation,
       with `interpolate(method="linear")` filling the days in between;
    3. a centred 3-day rolling mean over the result.

    There is no backward extension. One existed -- `LSTM_MIN_DAYS = 70`, a trend
    fitted with `linregress` and extrapolated before the first observation with
    `np.random.normal` noise -- and `e555560` removed it as a critical bug: the
    LSTM was training on fabricated scores, non-deterministically, so every
    restart produced a different model. Nothing here now emits a row earlier
    than the first real observation. (#122 attributes this removal to `00fabf4`;
    that commit raised `LSTM_MIN_ROWS` in `ensemble.py` and never touched this
    file.)

    What the series does *not* tell you
    -----------------------------------
    **Interpolated days are not marked.** The return value is two columns, `ds`
    and `y`, and nothing distinguishes a day that was observed from a day that
    was computed between two observations. Downstream -- every forecaster, every
    metric -- treats them identically because it cannot do otherwise. On the
    production `Evaluation` data that is not a rounding detail: 14 observed days
    across a 51-day span, including a 23-day gap (#75), so roughly 37 of 51
    points are manufactured here.

    **Observed values do not survive either.** Step 3 smooths the whole column,
    so a real measurement is replaced by an average of itself and its
    neighbours. An observed spike of 100 between two 10s comes back as 40. The
    caller therefore receives neither a marker for the invented rows nor the
    original numbers for the real ones.

    **The row count is a span, not an amount of evidence.** `len()` of this
    frame is the number of calendar days covered, and interpolation is what
    makes it large. `EnsembleForecaster.fit` reads `n_samples = len(df)` and
    picks its weighting tier from it, so a sparse series can be carried across a
    readiness threshold by the interpolation in step 2 rather than by having
    acquired the observations the threshold was meant to require. Interpolation
    fills a series; it does not add information, and it is not evidence that the
    data are sufficient for anything.

    Marking provenance would need a third column and a decision about what every
    consumer does with it; that is deliberately not made here (#122).

    Readiness lives elsewhere
    -------------------------
    This loader applies no threshold of its own -- the 70 above went out with
    the backfill. Whether a series is long enough for LSTM is decided by
    `LSTM_MIN_ROWS` in `app/services/forecasting/ensemble.py`, currently **33**,
    derived as seq_length(14) + max_lag(14) + buffer(5) and expected to move if
    those change (#115). Do not restate that number as a property of this
    function; `tests/test_data_loader_contract.py` checks the two agree.

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

    df = pd.DataFrame(recs).dropna()
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
