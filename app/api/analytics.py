"""Analytics endpoints for ESG benchmarking and model health."""
from fastapi import APIRouter, Query, HTTPException, Request, Depends
from pydantic import BaseModel, Field

from app.api.infra import admin_auth
from app.auth import require_auth

router = APIRouter(prefix="/analytics", tags=["analytics"])

_mc_limiter = None

def _get_mc_limiter():
    global _mc_limiter
    if _mc_limiter is None:
        from app.rate_limit import RateLimiter
        _mc_limiter = RateLimiter(max_requests=50, window_seconds=60)
    return _mc_limiter

def monte_carlo_dep(request: Request):
    ip = request.client.host if request.client else "127.0.0.1"
    _get_mc_limiter().check(f"mc:{ip}")



class MonteCarloRequest(BaseModel):
    name: str = Field("Test Project")
    budget: float = Field(100000, gt=0)
    co2_reduction: float = Field(50, gt=0)
    social_impact: float = Field(7, ge=1, le=10)
    duration_months: int = Field(24, ge=1, le=360)
    region: str = "Germany"
    simulations: int = Field(1000, ge=10, le=10000)


class ModelCompareRequest(BaseModel):
    name: str = "Test Project"
    budget: float = Field(100000, gt=0)
    co2_reduction: float = Field(50, gt=0)
    social_impact: float = Field(7, ge=1, le=10)
    duration_months: int = Field(24, ge=1, le=360)
    region: str = "Germany"


@router.post("/monte-carlo", summary="Monte Carlo risk simulation (GONE)")
async def monte_carlo_simulation(req: MonteCarloRequest, _: None = Depends(monte_carlo_dep)):
    """This endpoint has been removed.

    It used an old ESG formula that didn't see country data and computed
    probability from a manual formula instead of the RF model. Use the
    replacement at /api/v1/evaluate/monte-carlo.
    """
    raise HTTPException(
        status_code=410,
        detail={
            "error": "This endpoint has been removed",
            "reason": "Used an incorrect ESG formula that overestimated scores by ~16 points on average",
            "replacement": "/api/v1/evaluate/monte-carlo",
            "detail": "The shadow endpoint used a copy of an old ESG formula without country awareness "
                      "and a manual probability formula instead of the RandomForest model. "
                      "Use /api/v1/evaluate/monte-carlo for correct Monte Carlo simulations.",
        }
    )


@router.post("/model-compare", summary="Compare all ML models on a project")
async def model_compare(project: ModelCompareRequest):
    # The real models, not hand-written linear formulas (#323). This used to
    # compute four "probabilities" from a weighted sum of the inputs and label
    # them RandomForest / XGBoost / NeuralNet / StackingEnsemble -- numbers with
    # nothing to do with any model, served to the compare panel in app.js. It now
    # runs the same models the prediction routes do, through the shared blend, so
    # a change to a model shows here too.
    import app.main as m
    from app.api.predict import _base_probabilities, _blend
    from app.validators import ProjectInput as LegacyProjectInput

    legacy = LegacyProjectInput(
        budget=project.budget, co2_reduction=project.co2_reduction,
        social_impact=project.social_impact, duration_months=project.duration_months,
    )
    feats_9 = m.make_features_base(legacy)
    feats_7 = m.make_features_xgb(legacy)

    # RandomForest, and neural network only when its weights are loaded (#320).
    # XGBoost removed (FINDING-9): returns constant 93.73%. The ensemble is the
    # blend of whatever ran, as /predict/stacking serves it.
    probabilities = _base_probabilities(m.rf_model, m.xgb_model, m.nn_model, feats_9, feats_7)
    ens_p = _blend(probabilities)
    thr = m.best_threshold

    display = {"rf": "RandomForest", "nn": "NeuralNet"}
    models = {
        display[name]: {"probability": round(p * 100, 2), "prediction": int(p >= thr)}
        for name, p in probabilities.items()
    }
    models["StackingEnsemble"] = {"probability": round(ens_p * 100, 2), "prediction": int(ens_p >= thr)}
    best = max(models.items(), key=lambda x: x[1]["probability"])
    return {"models": models, "best_model": best[0], "threshold": thr}


@router.get("/country-benchmark/{country}", summary="ESG benchmark data for a country")
async def country_benchmark(country: str):
    from app.country_benchmarks import BENCHMARKS, GLOBAL_AVG
    from app.external_data import COUNTRY_ISO3

    iso3_to_name = {v: k for k, v in COUNTRY_ISO3.items()}
    normalized = country.strip()
    country_name = iso3_to_name.get(normalized.upper(), normalized)

    bench = BENCHMARKS.get(country_name, GLOBAL_AVG)
    return {
        "country": country_name if country_name in BENCHMARKS else "Global Average",
        "requested": country,
        "benchmarks": bench,
    }


@router.get("/country-ranking", summary="Global ESG ranking with pagination")
async def country_ranking(
    limit:  int = Query(20, ge=1, le=100, description="Results per page"),
    offset: int = Query(0,  ge=0,         description="Skip N results"),
):
    from app.country_benchmarks import BENCHMARKS
    ranked = sorted(BENCHMARKS.items(), key=lambda x: x[1]["esg_rank"])
    total  = len(ranked)
    return {
        "total":  total,
        "limit":  limit,
        "offset": offset,
        "data":   [{"country": name, **data} for name, data in ranked[offset:offset + limit]],
    }

@router.get("/predictions-log", dependencies=[Depends(require_auth)])
def get_predictions_log(limit: int = Query(100, ge=1, le=1000)):
    from app.main import get_db_sync
    from app.database import PredictionLog
    db = get_db_sync()
    try:
        rows = db.query(PredictionLog).order_by(
            PredictionLog.timestamp.desc()
        ).limit(limit).all()
        return [
            {c.name: getattr(r, c.name) for c in PredictionLog.__table__.columns}
            for r in rows
        ]
    finally:
        db.close()


@router.get("/metrics/model-health")
def model_health(_: None = Depends(admin_auth)):
    from app.main import rf_model, xgb_model, nn_model, ensemble_model_v2, model_metrics
    from app.database import PredictionLog
    from app.main import get_db_sync
    from datetime import datetime, timedelta
    from sqlalchemy import func

    db = get_db_sync()
    try:
        total_predictions = db.query(PredictionLog).count()

        last_24h = db.query(PredictionLog).filter(
            PredictionLog.timestamp >= datetime.utcnow() - timedelta(hours=24)
        ).count()

        avg_latency_overall = db.query(PredictionLog).filter(
            PredictionLog.latency_ms.isnot(None)
        ).with_entities(
            func.avg(PredictionLog.latency_ms)
        ).scalar()

        def _avg_latency_for(endpoint: str):
            q = db.query(PredictionLog).filter(
                PredictionLog.endpoint == endpoint,
                PredictionLog.latency_ms.isnot(None),
            ).with_entities(func.avg(PredictionLog.latency_ms))
            return q.scalar()

        avg_latency_rf = _avg_latency_for("predict_rf")
        avg_latency_nn = _avg_latency_for("predict_nn")
        avg_latency_eval = _avg_latency_for("evaluate")

        endpoint_counts = {
            row.endpoint: row.count
            for row in db.query(
                PredictionLog.endpoint, func.count().label("count")
            ).group_by(PredictionLog.endpoint)
        }
    finally:
        db.close()

    return {
        "models": {
            "random_forest": {"loaded": rf_model is not None, "type": "RandomForestClassifier"},
            "xgboost": {"loaded": xgb_model is not None, "type": "XGBClassifier", "note": "not used in blends (FINDING-9: returns constant)"},
            "pytorch_mlp": {"loaded": nn_model is not None, "type": "SoraNet"},
            "ensemble_v2": {"loaded": ensemble_model_v2 is not None, "type": "StackingClassifier"},
        },
        "training_metrics": model_metrics,
        "predictions": {
            "total": total_predictions,
            "last_24h": last_24h,
            "avg_latency_ms": round(avg_latency_overall, 2) if avg_latency_overall else None,
            "avg_latency_ms_rf": round(avg_latency_rf, 2) if avg_latency_rf else None,
            "avg_latency_ms_nn": round(avg_latency_nn, 2) if avg_latency_nn else None,
            "avg_latency_ms_evaluate": round(avg_latency_eval, 2) if avg_latency_eval else None,
            "endpoint_counts": endpoint_counts,
        },
        # Derived, not a literal: this endpoint is named model-health and reports
        # each model's loaded flag, so its status must reflect them. The champion
        # is RandomForest (the neural network is optional, #320; XGBoost removed
        # in FINDING-9). A missing RandomForest is "degraded".
        "status": "healthy" if rf_model is not None else "degraded",
    }
@router.get("/data-health")
def data_health(window_hours: int = 24):
    """Basic data & prediction quality summary over recent window.

    - window_hours: how many hours back from now to analyze.
    """
    from datetime import datetime, timedelta
    from sqlalchemy import func
    from app.main import get_db_sync
    from app.database import PredictionLog

    db = get_db_sync()
    try:
        since = datetime.utcnow() - timedelta(hours=window_hours)

        q = db.query(PredictionLog).filter(PredictionLog.timestamp >= since)
        total = q.count()
        if total == 0:
            return {
                "window_hours": window_hours,
                "total": 0,
                "null_rates": {},
                "out_of_range_rates": {},
                "prediction_distribution": {},
            }

        # Null rates for key features
        def _null_rate(column):
            return (
                db.query(func.count())
                .select_from(PredictionLog)
                .filter(
                    PredictionLog.timestamp >= since,
                    column.is_(None),
                )
                .scalar()
                / total
            )

        null_rates = {
            "budget": _null_rate(PredictionLog.budget),
            "co2_reduction": _null_rate(PredictionLog.co2_reduction),
            "social_impact": _null_rate(PredictionLog.social_impact),
            "duration_months": _null_rate(PredictionLog.duration_months),
            "category": _null_rate(PredictionLog.category),
            "region": _null_rate(PredictionLog.region),
        }

        # Out-of-range checks (simple heuristics)
        def _oor_rate(condition):
            return (
                db.query(func.count())
                .select_from(PredictionLog)
                .filter(
                    PredictionLog.timestamp >= since,
                    condition,
                )
                .scalar()
                / total
            )

        out_of_range_rates = {
            "budget_le_0": _oor_rate(PredictionLog.budget <= 0),
            "co2_not_0_100": _oor_rate(
                (PredictionLog.co2_reduction < 0)
                | (PredictionLog.co2_reduction > 100)
            ),
            "social_not_1_10": _oor_rate(
                (PredictionLog.social_impact < 1)
                | (PredictionLog.social_impact > 10)
            ),
            "duration_le_0": _oor_rate(PredictionLog.duration_months <= 0),
        }

        # Prediction distribution
        preds_q = q.filter(PredictionLog.prediction.isnot(None))
        preds_total = preds_q.count()
        if preds_total == 0:
            pred_dist = {}
        else:
            pos = preds_q.filter(PredictionLog.prediction == 1).count()
            neg = preds_q.filter(PredictionLog.prediction == 0).count()

            # Probability bins
            bins = {
                "0_20": 0,
                "20_40": 0,
                "40_60": 0,
                "60_80": 0,
                "80_100": 0,
            }
            for (prob,) in preds_q.with_entities(PredictionLog.probability):
                if prob is None:
                    continue
                if prob < 20:
                    bins["0_20"] += 1
                elif prob < 40:
                    bins["20_40"] += 1
                elif prob < 60:
                    bins["40_60"] += 1
                elif prob < 80:
                    bins["60_80"] += 1
                else:
                    bins["80_100"] += 1

            pred_dist = {
                "total_with_prediction": preds_total,
                "positive_frac": pos / preds_total if preds_total else None,
                "negative_frac": neg / preds_total if preds_total else None,
                "probability_bins": {
                    k: v / preds_total for k, v in bins.items()
                },
            }


        return {
            "window_hours": window_hours,
            "total": total,
            "null_rates": null_rates,
            "out_of_range_rates": out_of_range_rates,
            "prediction_distribution": pred_dist,
        }
    finally:
        db.close()


@router.get("/summary")
def analytics_summary(window_hours: int = 24, _: None = Depends(admin_auth)):
    from datetime import datetime, timedelta
    from sqlalchemy import func
    from app.main import get_db_sync, rf_model, xgb_model, nn_model, ensemble_model_v2, model_metrics
    from app.database import PredictionLog

    db = get_db_sync()
    try:
        since = datetime.utcnow() - timedelta(hours=window_hours)

        total = db.query(PredictionLog).filter(
            PredictionLog.timestamp >= since
        ).count()

        avg_latency = db.query(func.avg(PredictionLog.latency_ms)).filter(
            PredictionLog.timestamp >= since,
            PredictionLog.latency_ms.isnot(None)
        ).scalar()

        endpoint_counts = {
            row.endpoint: row.count
            for row in db.query(
                PredictionLog.endpoint, func.count().label("count")
            ).filter(
                PredictionLog.timestamp >= since
            ).group_by(PredictionLog.endpoint)
        }

        preds_q = db.query(PredictionLog).filter(
            PredictionLog.timestamp >= since,
            PredictionLog.prediction.isnot(None)
        )
        preds_total = preds_q.count()
        positive = preds_q.filter(PredictionLog.prediction == 1).count()
        negative = preds_q.filter(PredictionLog.prediction == 0).count()

        positive_frac = round(positive / preds_total, 4) if preds_total else None
        negative_frac = round(negative / preds_total, 4) if preds_total else None

        null_budget = db.query(func.count()).filter(
            PredictionLog.timestamp >= since,
            PredictionLog.budget.is_(None)
        ).scalar()

        null_co2 = db.query(func.count()).filter(
            PredictionLog.timestamp >= since,
            PredictionLog.co2_reduction.is_(None)
        ).scalar()

        null_social = db.query(func.count()).filter(
            PredictionLog.timestamp >= since,
            PredictionLog.social_impact.is_(None)
        ).scalar()

        null_duration = db.query(func.count()).filter(
            PredictionLog.timestamp >= since,
            PredictionLog.duration_months.is_(None)
        ).scalar()

        null_rate_total = 0.0
        if total > 0:
            null_rate_total = round(
                (null_budget + null_co2 + null_social + null_duration) / (total * 4),
                4
            )

        # The neural network is optional (#320) and does not count toward
        # readiness; `models_loaded.pytorch_mlp` below still reports its state.
        models_loaded = all([
            rf_model is not None,
            xgb_model is not None,
            ensemble_model_v2 is not None,
        ])

        insights = []

        if models_loaded:
            insights.append("All core ML models are loaded and available for inference.")
        else:
            insights.append("One or more ML models are not loaded, reducing platform readiness.")

        if avg_latency is not None:
            if avg_latency < 250:
                insights.append(f"Average inference latency is healthy at {round(avg_latency, 2)} ms.")
            elif avg_latency < 600:
                insights.append(f"Average inference latency is acceptable at {round(avg_latency, 2)} ms, but optimization headroom remains.")
            else:
                insights.append(f"Average inference latency is elevated at {round(avg_latency, 2)} ms and should be optimized before scale-up.")

        if null_rate_total == 0:
            insights.append("No missing values were detected in core production input fields during the selected window.")
        else:
            insights.append(f"Missing-value rate in core input fields is {null_rate_total * 100:.2f}% and requires data-quality controls.")

        if preds_total:
            if positive_frac is not None and positive_frac > 0.9:
                insights.append("Prediction distribution is strongly skewed toward positive outcomes, which may indicate favorable traffic or model bias.")
            elif positive_frac is not None and positive_frac < 0.1:
                insights.append("Prediction distribution is strongly skewed toward negative outcomes, which may indicate adverse traffic or model bias.")
            else:
                insights.append("Prediction distribution appears reasonably balanced for the observed production window.")

        if total >= 10:
            insights.append("The platform has already processed live production-style requests and is suitable for monitored pilot deployment.")
        else:
            insights.append("Observed request volume is still small; additional live traffic is needed for stronger production confidence.")

        readiness_score = 0
        if models_loaded:
            readiness_score += 30
        if avg_latency is not None and avg_latency < 250:
            readiness_score += 25
        elif avg_latency is not None and avg_latency < 600:
            readiness_score += 15
        if null_rate_total == 0:
            readiness_score += 20
        if total >= 10:
            readiness_score += 15
        if preds_total > 0:
            readiness_score += 10

        if readiness_score >= 85:
            readiness = "investor-demo ready"
        elif readiness_score >= 70:
            readiness = "pilot ready"
        elif readiness_score >= 50:
            readiness = "technical validation ready"
        else:
            readiness = "prototype stage"

        return {
            "window_hours": window_hours,
            "readiness": readiness,
            "readiness_score": readiness_score,
            "models_loaded": {
                "random_forest": rf_model is not None,
                "xgboost": xgb_model is not None,
                "pytorch_mlp": nn_model is not None,
                "ensemble_v2": ensemble_model_v2 is not None,
            },
            "training_metrics": model_metrics,
            "traffic": {
                "total_events": total,
                "endpoint_counts": endpoint_counts,
            },
            "performance": {
                "avg_latency_ms": round(avg_latency, 2) if avg_latency is not None else None,
            },
            "prediction_quality_proxy": {
                "total_predictions": preds_total,
                "positive_fraction": positive_frac,
                "negative_fraction": negative_frac,
            },
            "data_quality": {
                "core_input_missing_rate": null_rate_total,
            },
            "insights": insights,
        }
    finally:
        db.close()
