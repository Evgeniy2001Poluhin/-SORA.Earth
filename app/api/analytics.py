"""Monte Carlo simulation endpoint for risk analysis."""
import asyncio
import os
from concurrent.futures import ProcessPoolExecutor

import numpy as np
from fastapi import APIRouter, Query, HTTPException, Request, Depends
from pydantic import BaseModel, Field

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
        risk_label = "LOW"
    elif mean_score >= 50 and mean_prob >= 50:
        risk_label = "MEDIUM"
    else:
        risk_label = "HIGH"

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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Simulation failed: {str(e)}")
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Model inference failed: {str(e)}")

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