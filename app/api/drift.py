"""Per-feature KS drift report for GET /api/v1/model/drift.

This endpoint answers one question -- how far each feature's recent
distribution has moved from the training baseline -- and it is the first
contract migrated under docs/API_CONTRACT_ROADMAP.md (#239).

It is **not** the aggregate drift verdict. That is
`GET /api/v1/mlops/drift`, backed by `drift_detector.check_drift()`, and it is
what the drift page's STATUS and DRIFT SCORE come from. Two endpoints once used
the word "drift" for different things in different shapes; this one is now
explicitly the KS report and says so in its schema.

What changed, and why: the four branches below used to answer 200 with four
incompatible bodies, calling the verdict `drift` twice, `drift_detected` once,
and omitting it once. The only consumer -- the frontend's `ksReport()` -- reads
`features` and nothing else, so it never noticed; but "no prediction log yet"
reached the screen as "0 features", which reads as a measurement.
"""
import os
import pandas as pd
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.schemas import (
    KsFeatureStat,
    ModelDriftMeasured,
    ModelDriftNotMeasured,
    ModelDriftResponse,
    ModelDriftUnavailable,
)

try:
    from scipy import stats as _stats
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

#: Rows required before a KS test is worth running. Unchanged by #239 -- this
#: PR moves the contract, not the algorithm or its thresholds.
MIN_WINDOW_ROWS = 10
SIGNIFICANCE = 0.05

router = APIRouter(prefix="/model", tags=["mlops"])
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PRED_LOG = os.path.join(ROOT_DIR, "data", "predictions_log.csv")
PROJ_CSV = os.path.join(ROOT_DIR, "data", "projects.csv")
COLS = ["budget", "co2_reduction", "social_impact", "duration_months"]

def _unavailable(window: int, reason: str) -> JSONResponse:
    """A fault in the deployment, not an answer about drift.

    503 rather than 200: scipy is a declared dependency (scipy==1.13.1 in both
    requirements files) and the baseline file ships with the repository, so
    either being absent means this instance cannot do its job. Answering 200
    with a falsy verdict would tell an operator the model is fine.

    Returned as a JSONResponse rather than the model: `response_model` is the
    200 union, and FastAPI validates every returned value against it, so
    handing back `ModelDriftUnavailable` produced a 500 instead of a 503. The
    body is still built from the model, so the shape stays declared rather than
    hand-written -- and `responses={503: ...}` keeps it in the schema.
    """
    body = ModelDriftUnavailable(
        status="unavailable", window=window, observations=0, features={}, reason_code=reason
    )
    return JSONResponse(status_code=503, content=body.model_dump())


@router.get(
    "/drift",
    response_model=ModelDriftResponse,
    responses={503: {"model": ModelDriftUnavailable, "description": "The KS test could not be run."}},
    summary="Per-feature KS drift report (not the aggregate verdict)",
)
def check_drift(window: int = 50):
    if not HAS_SCIPY:
        return _unavailable(window, "scipy_missing")
    if not os.path.exists(PROJ_CSV):
        return _unavailable(window, "baseline_missing")
    baseline = pd.read_csv(PROJ_CSV)
    if not os.path.exists(PRED_LOG):
        return ModelDriftNotMeasured(
            status="no_log", window=window, observations=0, features={},
            reason_code="prediction_log_absent",
        )
    recent = pd.read_csv(PRED_LOG).tail(window)
    if len(recent) < MIN_WINDOW_ROWS:
        return ModelDriftNotMeasured(
            status="insufficient_data", window=window, observations=len(recent), features={},
            reason_code="below_minimum_window",
        )
    results = {}
    drift_any = False
    for col in COLS:
        if col not in baseline.columns or col not in recent.columns:
            continue
        stat, p = _stats.ks_2samp(baseline[col].dropna().values, recent[col].dropna().values)
        d = bool(p < SIGNIFICANCE)
        if d: drift_any = True
        results[col] = KsFeatureStat(ks_stat=round(float(stat),4), p_value=round(float(p),4), drift=d)
        # MLflow drift event logging (Sprint 7 closure)
    if drift_any:
        try:
            drifted = [c for c, m in results.items() if m.drift]
            payload = {
                "drift_detected": True,
                "drift_score": round(len(drifted) / max(len(results), 1), 2),
                "drifted_features": drifted,
                "features_analyzed": list(results.keys()),
                "reference_samples": len(baseline) if baseline is not None else 0,
                "current_samples": len(recent) if recent is not None else 0,
                "ks_test": {k: v.model_dump() for k, v in results.items()},
                "psi": {},
            }
            from app.mlflow_tracking import log_drift_event as _lde
            _lde(payload, baseline_id="model_drift_endpoint")
        except Exception as _e:
            try:
                print("[mlflow_drift hook] failed:", _e)
            except Exception:
                pass
    return ModelDriftMeasured(
        status="ok", drift_detected=drift_any, window=window,
        observations=len(recent), features=results, reason_code=None,
    )



@router.get("/drift/mlflow-history", tags=["mlops"])
def drift_mlflow_history(limit: int = 50):
    """Return recent drift events recorded in MLflow (tag type=drift_event)."""
    try:
        import mlflow as _ml
        runs = _ml.search_runs(
            filter_string="tags.type = 'drift_event'",
            order_by=["start_time DESC"],
            max_results=int(max(1, min(limit, 500))),
        )
    except Exception as e:
        return {"events": [], "count": 0, "error": str(e)}
    if runs is None or getattr(runs, "empty", True):
        return {"events": [], "count": 0}
    keep = [c for c in [
        "run_id", "start_time", "experiment_id",
        "metrics.drift_score", "metrics.drifted_features_count",
        "metrics.n_samples_ref", "metrics.n_samples_cur",
        "tags.baseline_id", "params.drifted_features",
    ] if c in runs.columns]
    df = runs[keep].copy()
    if "start_time" in df.columns:
        df["start_time"] = df["start_time"].astype(str)
    out = df.to_dict(orient="records")
    return {"events": out, "count": len(out)}
