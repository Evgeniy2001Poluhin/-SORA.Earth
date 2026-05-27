from fastapi import APIRouter, HTTPException
from app.drift_detection import run_drift_analysis
from app.services.alerts import send_alert
import pandas as pd
import logging
import os

router = APIRouter()
logger = logging.getLogger(__name__)

FEATURE_COLS = ['budget', 'co2_reduction', 'social_impact', 'duration_months']

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
        max_psi = max((float(v) for v in psi.values() if isinstance(v, (int, float))), default=0.0)
        result["max_psi"] = max_psi
        result["alert_threshold"] = threshold
        if max_psi > threshold:
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


@router.post("/drift/test-alert", summary="Manual drift alert test (Slack/Telegram/Email)")
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
