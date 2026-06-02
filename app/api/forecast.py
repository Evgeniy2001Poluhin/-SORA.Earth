from fastapi import APIRouter, Query
from datetime import timedelta
import numpy as np
import pandas as pd

router = APIRouter(tags=["forecast"])

@router.get("/forecast")
def forecast(horizon: int = Query(30, ge=1, le=180),
             metric: str = Query("score", pattern="^(score|prob)$")):
    from app.main import get_db_sync
    from app.database import Evaluation

    db = get_db_sync()
    try:
        rows = db.query(Evaluation).order_by(Evaluation.created_at.asc()).all()
    finally:
        db.close()

    if not rows:
        return {"history": [], "forecast": [], "model": "linear-trend", "metric": metric}

    recs = [{
        "ds": pd.to_datetime(str(r.created_at)[:19]),
        "y": float(r.total_score if metric == "score" else r.success_probability),
    } for r in rows]
    df = pd.DataFrame(recs).dropna()
    # aggregate to daily mean
    df = df.set_index("ds").resample("D")["y"].mean().dropna().reset_index()
    df["y"] = df["y"].rolling(7, min_periods=1, center=True).mean()

    if len(df) < 3:
        return {"history": [{"ds": str(d.date()), "y": round(v, 3)}
                            for d, v in zip(df["ds"], df["y"])],
                "forecast": [], "model": "linear-trend", "metric": metric}

    t0 = df["ds"].min()
    x = (df["ds"] - t0).dt.days.to_numpy(dtype=float)
    y = df["y"].to_numpy(dtype=float)

    coeffs = np.polyfit(x, y, 1)
    fit = np.poly1d(coeffs)
    resid = y - fit(x)
    sigma = float(np.std(resid, ddof=2)) if len(y) > 2 else 0.0
    ci = 1.96 * sigma

    last_day = int(x.max())
    fut_x = np.arange(last_day + 1, last_day + 1 + horizon, dtype=float)
    yhat = fit(fut_x)
    lo, hi = (0.0, 100.0) if metric == "score" else (0.0, 1.0)

    forecast_pts = [{
        "ds": str((t0 + timedelta(days=int(fx))).date()),
        "yhat": round(float(np.clip(v, lo, hi)), 3),
        "yhat_lower": round(float(np.clip(v - ci, lo, hi)), 3),
        "yhat_upper": round(float(np.clip(v + ci, lo, hi)), 3),
    } for fx, v in zip(fut_x, yhat)]

    return {
        "history": [{"ds": str(d.date()), "y": round(float(v), 3)}
                    for d, v in zip(df["ds"], df["y"])],
        "forecast": forecast_pts,
        "model": "linear-trend",
        "metric": metric,
        "slope_per_day": round(float(coeffs[0]), 5),
        "ci95": round(ci, 3),
    }
