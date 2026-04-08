"""Monte Carlo simulation endpoint for risk analysis."""
import asyncio
import os
from concurrent.futures import ProcessPoolExecutor

import numpy as np
from fastapi import APIRouter, Query, HTTPException, Request, Depends
from pydantic import BaseModel, Field

from app.api.infra import admin_auth

router = APIRouter(prefix="/analytics", tags=["analytics"])
_executor = ProcessPoolExecutor(max_workers=os.cpu_count() or 4)

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
    co2_reduction: float = Field(50, gt=0, le=100)
    social_impact: float = Field(7, ge=1, le=10)
    duration_months: int = Field(24, ge=1, le=360)
    region: str = "Germany"
    simulations: int = Field(1000, ge=10, le=10000)


class ModelCompareRequest(BaseModel):
    name: str = "Test Project"
    budget: float = Field(100000, gt=0)
    co2_reduction: float = Field(50, gt=0, le=100)
    social_impact: float = Field(7, ge=1, le=10)
    duration_months: int = Field(24, ge=1, le=360)
    region: str = "Germany"


def _run_monte_carlo(data: dict) -> dict:
    from app.main import COUNTRIES, REGIONAL_FACTORS, rf_model, make_features
    from app.schemas import ProjectInput as Project

    n = min(data['simulations'], 10000)
    rng = np.random.default_rng()

    budget_arr   = np.clip(rng.normal(data['budget'],          data['budget'] * 0.15,         n), 1000, None)
    co2_arr      = np.clip(rng.normal(data['co2_reduction'],   data['co2_reduction'] * 0.2,   n), 1, 100)
    social_arr   = np.clip(rng.normal(data['social_impact'],   1.0,                           n), 1, 10)
    duration_arr = np.clip(rng.normal(data['duration_months'], data['duration_months'] * 0.1, n).astype(int), 1, None)

    cdata      = COUNTRIES.get(data['region'], {'region': 'Europe'})
    region_str = cdata.get('region', 'Europe')
    rf = REGIONAL_FACTORS.get(region_str, REGIONAL_FACTORS['Europe'])

    score_env = np.minimum(co2_arr / 100.0 * rf['env_mult'] + rf['renewable_bonus'], 1.0)
    score_soc = np.minimum(social_arr / 10.0 * rf['soc_mult'], 1.0)
    score_eco = np.minimum(1.0 / (1.0 + np.exp(-0.00005 * (budget_arr - 50000))) * rf['eco_mult'], 1.0)
    dur_factor = np.where(duration_arr > 48, 0.9, np.where(duration_arr > 36, 0.95, 1.0))
    scores = np.minimum((score_env * 0.4 + score_soc * 0.3 + score_eco * 0.3) * dur_factor * 100, 100.0)

    sample_n = min(n, 200)
    idx = rng.choice(n, sample_n, replace=False)
    probs_sample = []
    for i in idx:
        p = Project(name=data['name'], budget=float(budget_arr[i]),
                    co2_reduction=float(co2_arr[i]), social_impact=float(social_arr[i]),
                    duration_months=int(duration_arr[i]), region=data['region'])
        feats = make_features(p)
        probs_sample.append(float(rf_model.predict_proba(feats)[0][1]) * 100)
    probs = np.interp(scores, np.sort(scores[idx]), np.array(probs_sample)[np.argsort(scores[idx])])

    # Интерполируем на весь массив через скор (линейная аппроксимация)
    probs = np.interp(scores, np.sort(scores[idx]), np.array(probs_sample)[np.argsort(scores[idx])])



    low    = float(np.mean((scores >= 75) & (probs >= 70))) * 100
    medium = float(np.mean((scores >= 50) & (scores < 75))) * 100
    high   = float(np.mean(scores < 50)) * 100
    total  = low + medium + high
    if total > 0:
        low, medium, high = low/total*100, medium/total*100, high/total*100

    def p(arr, q): return round(float(np.percentile(arr, q)), 2)

    mean_score = float(np.mean(scores))
    mean_prob  = float(np.mean(probs))
    if mean_score >= 75 and mean_prob >= 70:
        risk_label = "LOW"  # pragma: no cover
    elif mean_score >= 50 and mean_prob >= 50:
        risk_label = "MEDIUM"
    else:
        risk_label = "HIGH"  # pragma: no cover

    return {
        "simulations": n,
        "score_stats": {
            "mean": round(mean_score, 2), "std": round(float(np.std(scores)), 2),
            "min": p(scores, 0), "max": p(scores, 100),
            "p5": p(scores, 5), "p25": p(scores, 25),
            "median": round(float(np.median(scores)), 2),
            "p75": p(scores, 75), "p95": p(scores, 95),
        },
        "probability_stats": {
            "mean": round(mean_prob, 2), "std": round(float(np.std(probs)), 2),
            "min": p(probs, 0), "max": p(probs, 100),
            "median": round(float(np.median(probs)), 2),
        },
        "risk_distribution": {
            "low_risk_pct":    round(low, 1),
            "medium_risk_pct": round(medium, 1),
            "high_risk_pct":   round(high, 1),
        },
        "risk_summary": risk_label,
    }


@router.post("/monte-carlo", summary="Monte Carlo risk simulation")
async def monte_carlo_simulation(req: MonteCarloRequest, _: None = Depends(monte_carlo_dep)):
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(_executor, _run_monte_carlo, req.dict())
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Simulation failed: {str(e)}")  # pragma: no cover
    return result


@router.post("/model-compare", summary="Compare all ML models on a project")
async def model_compare(project: ModelCompareRequest):
    from app.main import rf_model, xgb_model, nn_model, ensemble_model, best_threshold, make_features
    from app.validators import ProjectInput as LegacyProjectInput
    import torch

    try:
        feats = make_features(LegacyProjectInput(
            budget=project.budget, co2_reduction=project.co2_reduction,
            social_impact=project.social_impact, duration_months=project.duration_months,
        ))
        rf_p  = float(rf_model.predict_proba(feats)[0][1])
        xgb_p = float(xgb_model.predict_proba(feats)[0][1])
        x     = torch.tensor(feats.values, dtype=torch.float32)
        nn_p  = float(nn_model(x).detach().numpy()[0][0])
        ens_p = float(ensemble_model.predict_proba(feats)[0][1])
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Model inference failed: {str(e)}")  # pragma: no cover

    models = {
        "RandomForest":     {"probability": round(rf_p  * 100, 2), "prediction": int(rf_p  >= best_threshold)},
        "XGBoost":          {"probability": round(xgb_p * 100, 2), "prediction": int(xgb_p >= best_threshold)},
        "NeuralNet":        {"probability": round(nn_p  * 100, 2), "prediction": int(nn_p  >= best_threshold)},
        "StackingEnsemble": {"probability": round(ens_p * 100, 2), "prediction": int(ens_p >= best_threshold)},
    }
    best = max(models.items(), key=lambda x: x[1]["probability"])
    return {"models": models, "best_model": best[0], "threshold": best_threshold}


@router.get("/country-benchmark/{country}", summary="ESG benchmark data for a country")
async def country_benchmark(country: str):
    from app.country_benchmarks import BENCHMARKS, GLOBAL_AVG
    bench = BENCHMARKS.get(country, GLOBAL_AVG)
    return {
        "country": country if country in BENCHMARKS else "Global Average",
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

@router.get("/predictions-log")
def get_predictions_log(limit: int = 100):
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
            "xgboost": {"loaded": xgb_model is not None, "type": "XGBClassifier"},
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
        "status": "healthy",
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

        models_loaded = all([
            rf_model is not None,
            xgb_model is not None,
            nn_model is not None,
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
