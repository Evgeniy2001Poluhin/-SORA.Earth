"""Centralized data loading for forecast training."""

import logging
import pandas as pd
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
    3. every row is marked `is_observed`: True where a value was recorded,
       False where step 2 manufactured it.

    No row is produced outside the observed range: there is no backward or
    forward extension, and nothing is emitted before the first or after the last
    observation.

    What the caller has to account for:

    * **`is_observed` says which rows are real.** A row is True when a value was
      recorded for that date and False when the interpolation manufactured it.
      Both carry a `y`; only one is a measurement.
    * **Observed values are returned as measured.** A centred rolling mean used
      to be applied to the whole column, so a value at an observed date was not
      the measurement taken that day. It is now.
    * **`len()` is a calendar span, not a count of observations.** Interpolation
      is what makes it large. A long, sparsely observed range and a short, fully
      observed one return the same number of rows, so the length of this frame
      is not evidence that the data are sufficient for anything. The count is
      `int(df["is_observed"].sum())`; there is deliberately no second field
      holding it, because two records of one fact drift apart.

    Readiness is not decided here. This loader applies no threshold of its own;
    whether a series supports a given model is ensemble policy, in
    `app/services/forecasting/ensemble.py`.

    Args:
        db: SQLAlchemy database session
        metric: Target metric name (score/prob/co2_reduction)

    Returns:
        DataFrame with columns ["ds", "y", "is_observed"] -- one row per calendar
        day from the first to the last observation. Empty (same columns) when
        there are no rows or no values for the metric.
    """
    from app.database import Evaluation

    rows = db.query(Evaluation).order_by(Evaluation.created_at.asc()).all()
    if not rows:
        return pd.DataFrame(columns=["ds", "y", "is_observed"])

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
        # The same three columns as the populated path. An empty frame with a
        # different shape is the defect this loader already had once: a caller
        # doing df["is_observed"] would get a KeyError on the one input it is
        # most likely to hit, and the docstring would be wrong about the case
        # nobody looks at.
        return df.assign(is_observed=pd.Series(dtype=bool))

    # Aggregate to daily mean
    df = df.set_index("ds").resample("D")["y"].mean().reset_index()

    # Fill ALL date gaps with interpolation (creates continuous time series)
    date_range = pd.date_range(start=df["ds"].min(), end=df["ds"].max(), freq="D")
    # Taken from the values, not from the index. `resample("D")` above already
    # emits a row for every calendar day, with NaN where nothing was recorded,
    # so by this point "the date is present" is true of every date and says
    # nothing. What still distinguishes them is whether a value survived.
    #
    # Measured while writing this: reading the index instead marked all five
    # rows of a two-observation series as observed -- the exact confusion #132
    # is about, reproduced inside its own fix.
    df = df.set_index("ds")
    observed_dates = set(df.index[df["y"].notna()])
    df = df.reindex(date_range).interpolate(method="linear").reset_index()
    df.columns = ["ds", "y"]
    df["is_observed"] = df["ds"].isin(observed_dates)

    # REMOVED: Synthetic extension with random noise pollutes training
    # Instead: Lower LSTM threshold to work with real interpolated data
    # Original issue: np.random.normal() creates non-deterministic data
    # → Different model every restart, metrics meaningless, forecasts unstable

    # REMOVED: a centred 3-day rolling mean, applied to the whole column.
    #
    # It was described as light smoothing to reduce noise. Measured, it does
    # the opposite of what that implies: on an interpolated stretch the values
    # are already linear and a centred mean is the identity there -- the shift
    # is exactly 0.000000. The only rows it can change are the observed ones.
    # Three consecutive observations 10, 100, 10 came back as 55, 40, 55, so a
    # measured peak of 100 was reported as 40.
    #
    # A step that is the identity everywhere except on measurements, and whose
    # only effect is to alter measurements, cannot be defended as noise
    # reduction. Consumers wanting a smoothed series can smooth one; they
    # cannot recover a measurement this had already overwritten.

    log.info("Time series for %s: %d days spanning %s..%s, %d observed",
             metric, len(df), df["ds"].min().date(), df["ds"].max().date(),
             int(df["is_observed"].sum()))
    return df
