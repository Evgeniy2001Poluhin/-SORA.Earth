"""Monte Carlo simulation endpoint for risk analysis."""
from fastapi import APIRouter
from pydantic import BaseModel
import numpy as np

router = APIRouter(prefix="/analytics", tags=["analytics"])


class MonteCarloRequest(BaseModel):
    name: str = "Test Project"
    budget: float = 100000
    co2_reduction: float = 50
    social_impact: float = 7
    duration_months: int = 24
    region: str = "Germany"
    simulations: int = 1000


@router.post("/monte-carlo")
def monte_carlo_simulation(req: MonteCarloRequest):
    from app.main import calculate_esg, COUNTRIES
    from app.schemas import ProjectInput as Project

    np.random.seed(42)
    n = min(req.simulations, 10000)
    scores, probabilities = [], []

    for _ in range(n):
        budget = max(1000, np.random.normal(req.budget, req.budget * 0.15))
        co2 = np.clip(np.random.normal(req.co2_reduction, req.co2_reduction * 0.2), 1, 100)
        social = np.clip(np.random.normal(req.social_impact, 1.0), 1, 10)
        duration = max(1, int(np.random.normal(req.duration_months, req.duration_months * 0.1)))

        project = Project(
            name=req.name, budget=budget, co2_reduction=co2,
            social_impact=social, duration_months=duration, region=req.region,
        )
        cdata = COUNTRIES.get(req.region, {"region": "Europe"})
        result = calculate_esg(project, cdata.get("region", "Europe"))
        scores.append(result["total_score"])
        probabilities.append(result["success_probability"])

    scores = np.array(scores)
    probs = np.array(probabilities)

    return {
        "simulations": n,
        "score_stats": {
            "mean": round(float(np.mean(scores)), 2),
            "std": round(float(np.std(scores)), 2),
            "min": round(float(np.min(scores)), 2),
            "max": round(float(np.max(scores)), 2),
            "p5": round(float(np.percentile(scores, 5)), 2),
            "p25": round(float(np.percentile(scores, 25)), 2),
            "median": round(float(np.median(scores)), 2),
            "p75": round(float(np.percentile(scores, 75)), 2),
            "p95": round(float(np.percentile(scores, 95)), 2),
        },
        "probability_stats": {
            "mean": round(float(np.mean(probs)), 2),
            "std": round(float(np.std(probs)), 2),
            "min": round(float(np.min(probs)), 2),
            "max": round(float(np.max(probs)), 2),
            "median": round(float(np.median(probs)), 2),
        },
        "risk_distribution": {
            "low_risk_pct": round(float(np.mean((scores >= 75) & (probs >= 70))) * 100, 1),
            "medium_risk_pct": round(float(np.mean((scores >= 50) & (scores < 75))) * 100, 1),
            "high_risk_pct": round(float(np.mean(scores < 50)) * 100, 1),
        },
    }


from app.country_benchmarks import BENCHMARKS, GLOBAL_AVG


class ModelCompareRequest(BaseModel):
    name: str = "Test Project"
    budget: float = 100000
    co2_reduction: float = 50
    social_impact: float = 7
    duration_months: int = 24
    region: str = "Germany"


@router.post("/model-compare")
def model_compare(project: ModelCompareRequest):
    from app.main import rf_model, xgb_model, nn_model, ensemble_model, best_threshold, make_features
    from app.validators import ProjectInput as LegacyProjectInput
    import torch

    feats = make_features(LegacyProjectInput(
        budget=project.budget, co2_reduction=project.co2_reduction,
        social_impact=project.social_impact, duration_months=project.duration_months,
    ))
    rf_p = float(rf_model.predict_proba(feats)[0][1])
    xgb_p = float(xgb_model.predict_proba(feats)[0][1])
    x = torch.tensor(feats.values, dtype=torch.float32)
    nn_p = float(nn_model(x).detach().numpy()[0][0])
    ens_p = float(ensemble_model.predict_proba(feats)[0][1])

    models = {
        "RandomForest": {"probability": round(rf_p * 100, 2), "prediction": int(rf_p >= best_threshold)},
        "XGBoost": {"probability": round(xgb_p * 100, 2), "prediction": int(xgb_p >= best_threshold)},
        "NeuralNet": {"probability": round(nn_p * 100, 2), "prediction": int(nn_p >= best_threshold)},
        "StackingEnsemble": {"probability": round(ens_p * 100, 2), "prediction": int(ens_p >= best_threshold)},
    }
    best = max(models.items(), key=lambda x: x[1]["probability"])
    return {"models": models, "best_model": best[0], "threshold": best_threshold}


@router.get("/country-benchmark/{country}")
def country_benchmark(country: str):
    bench = BENCHMARKS.get(country, GLOBAL_AVG)
    return {
        "country": country if country in BENCHMARKS else "Global Average",
        "benchmarks": bench,
    }


@router.get("/country-ranking")
def country_ranking():
    ranked = sorted(BENCHMARKS.items(), key=lambda x: x[1]["esg_rank"])
    return [{"country": name, **data} for name, data in ranked]
