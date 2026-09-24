from fastapi import APIRouter, Depends, HTTPException
from app.auth import require_admin
from app.drift_detection import run_drift_analysis
from app.services.alerts import send_alert
import pandas as pd
import logging
import os

router = APIRouter()
logger = logging.getLogger(__name__)

FEATURE_COLS = ['budget', 'co2_reduction', 'social_impact', 'duration_months']


def _max_psi(psi):
    """The largest PSI in the block, or None when there is none to take.

    `population_stability_index` returns one dict per column --
    `{"psi": float, "drift": bool, "severity": str}` -- and this used to filter
    for `isinstance(v, (int, float))`. Every value is a dict, so the filter kept
    nothing, the reduction fell through to its default, and `max_psi` was 0.0 on
    every call. 0.0 is below every threshold, so `send_alert` was never reached:
    the alert on /drift/analyze could not fire.

    Measured 2026-09-21 on reference N(100, 10) against current N(400, 10):
    psi 11.5128 on all four columns, `max_psi` reported 0.0, `alert_sent` false.
    The response carried both at once.

    None rather than 0.0 when nothing can be reduced. A reduction over no values
    means the PSI was not computed, which is not a measurement of no drift --
    the distinction #369 was opened for, one module over. The caller skips the
    alert in that case and says `max_psi: null` rather than a number it does not
    have.
    """
    values = [
        float(entry["psi"])
        for entry in psi.values()
        if isinstance(entry, dict) and isinstance(entry.get("psi"), (int, float))
    ]
    return max(values) if values else None

@router.post("/drift/analyze", summary="Analyze data drift: training vs recent predictions")
def analyze_drift(window_days: int = 7):
    """Compare training data distribution vs recent evaluations."""
    try:
        from app.training import load_training_data
        full_df = load_training_data()
    except Exception as e:
        raise HTTPException(500, f"Cannot load data: {e}")

    if len(full_df) < 20:
        raise HTTPException(400, "Not enough data for drift analysis")

    # Split: first 70% = reference, last 30% = current
    split = int(len(full_df) * 0.7)
    ref = full_df.iloc[:split]
    cur = full_df.iloc[split:]

    result = run_drift_analysis(ref, cur, FEATURE_COLS)
    try:
        threshold = float(os.getenv("DRIFT_PSI_THRESHOLD", "0.2"))
        psi = result.get("psi", {}) if isinstance(result, dict) else {}
        max_psi = _max_psi(psi)
        result["max_psi"] = max_psi
        result["alert_threshold"] = threshold
        if max_psi is not None and max_psi > threshold:
            sev = "critical" if max_psi > threshold * 2 else "warning"
            top = sorted(psi.items(), key=lambda kv: -float(kv[1]))[:3]
            details = ", ".join(f"{k}={float(v):.3f}" for k, v in top)
            send_alert(f"Data drift detected. Max PSI={max_psi:.3f} (threshold {threshold}). Top: {details}", severity=sev, title="SORA.Earth Drift Alert")
            result["alert_sent"] = True
        else:
            result["alert_sent"] = False
    except Exception as e:
        logger.warning(f"alert hook failed: {e}")
        result["alert_error"] = str(e)
    return result


@router.post("/drift/test-alert", summary="Manual drift alert test (Slack/Telegram/Email)",
             dependencies=[Depends(require_admin)])
def test_alert(severity: str = "warning", message: str = "Test alert from /drift/test-alert"):
    return send_alert(message=message, severity=severity, title="SORA.Earth Test Alert")


@router.post("/drift/compare", summary="Compare two time periods for drift")
def compare_periods(period1_days: int = 30, period2_days: int = 7):
    """Compare older period vs recent period."""
    try:
        from app.database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        df = pd.read_sql(text("SELECT * FROM evaluations ORDER BY created_at"), db.bind)
        db.close()
    except Exception as e:
        raise HTTPException(500, f"Cannot load from DB: {e}")

    if len(df) < 20:
        raise HTTPException(400, "Not enough data")

    # Use created_at to split periods
    if 'created_at' in df.columns:
        df['created_at'] = pd.to_datetime(df['created_at'])
        cutoff = df['created_at'].max() - pd.Timedelta(days=period2_days)
        ref = df[df['created_at'] <= cutoff]
        cur = df[df['created_at'] > cutoff]
    else:
        split = int(len(df) * 0.7)
        ref = df.iloc[:split]
        cur = df.iloc[split:]

    if len(ref) < 10 or len(cur) < 10:
        raise HTTPException(400, f"Insufficient data: ref={len(ref)}, cur={len(cur)}")

    result = run_drift_analysis(ref, cur, FEATURE_COLS)
    return result


@router.get("/drift/features/stats", summary="Current feature statistics")
def feature_stats():
    """Get feature statistics for the full dataset."""
    from app.drift_detection import feature_statistics
    try:
        from app.training import load_training_data
        df = load_training_data()
    except Exception as e:
        raise HTTPException(500, str(e))

    cols = [c for c in FEATURE_COLS if c in df.columns]
    return {"samples": len(df), "stats": feature_statistics(df[cols])}
