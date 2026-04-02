import random
from fastapi import APIRouter
from app.schemas import ProjectInput as Project
from app.country_benchmarks import BENCHMARKS, GLOBAL_AVG

router = APIRouter()

@router.get("/analytics/country-benchmark/{country}")
def country_benchmark(country: str):
    bench = BENCHMARKS.get(country, GLOBAL_AVG)
    return {
        "country": country if country in BENCHMARKS else "Global Average",
        "benchmarks": {
            "co2_per_capita": bench["co2_per_capita"],
            "renewable_share": bench["renewable_share"],
            "esg_rank": bench["esg_rank"],
            "hdi": bench["hdi"],
        }
    }

@router.get("/analytics/country-ranking")
def country_ranking():
    ranking = [{"country": c, "esg_rank": d["esg_rank"], "co2_per_capita": d["co2_per_capita"], "renewable_share": d["renewable_share"], "hdi": d["hdi"]} for c, d in BENCHMARKS.items()]
    ranking.sort(key=lambda x: x["esg_rank"])
    return ranking

@router.post("/analytics/montecarlo")
def monte_carlo(project: Project, n: int = 1000):
    from app.main import calculate_esg, COUNTRIES
    cdata = COUNTRIES.get(project.region or "Germany", {"region": "Europe"})
    region_name = cdata.get("region", "Europe")
    base = calculate_esg(project, region_name)
    scores = []
    for _ in range(n):
        d = project.model_dump()
        d["budget"] = d["budget"] * random.uniform(0.8, 1.2)
        d["co2_reduction"] = max(0, d["co2_reduction"] + random.uniform(-20, 20))
        d["social_impact"] = max(0, min(10, d["social_impact"] + random.uniform(-1, 1)))
        d["duration_months"] = max(1, int(d["duration_months"] + random.randint(-3, 3)))
        mod = Project(**d)
        r = calculate_esg(mod, region_name)
        scores.append(r["total_score"])
    scores.sort()
    return {"base_score": base["total_score"], "simulations": n, "mean": round(sum(scores)/len(scores),2), "median": round(scores[len(scores)//2],2), "p5": round(scores[int(n*0.05)],2), "p95": round(scores[int(n*0.95)],2), "min": round(min(scores),2), "max": round(max(scores),2)}

@router.post("/analytics/model-compare")
def model_compare(project: Project):
    from app.main import rf_model, xgb_model, nn_model, ensemble_model, best_threshold, make_features
    from app.validators import ProjectInput as LegacyProjectInput
    import torch, numpy as np
    feats = make_features(LegacyProjectInput(budget=project.budget, co2_reduction=project.co2_reduction, social_impact=project.social_impact, duration_months=project.duration_months))
    rf_p = float(rf_model.predict_proba(feats)[0][1])
    xgb_p = float(xgb_model.predict_proba(feats)[0][1])
    x = torch.tensor(feats.values, dtype=torch.float32)
    nn_p = float(nn_model(x).detach().numpy()[0][0])
    ens_p = float(ensemble_model.predict_proba(feats)[0][1])
    models = {"RandomForest": {"probability": round(rf_p*100,2), "prediction": int(rf_p>=best_threshold)}, "XGBoost": {"probability": round(xgb_p*100,2), "prediction": int(xgb_p>=best_threshold)}, "NeuralNet": {"probability": round(nn_p*100,2), "prediction": int(nn_p>=best_threshold)}, "StackingEnsemble": {"probability": round(ens_p*100,2), "prediction": int(ens_p>=best_threshold)}}
    return {"models": models, "threshold": best_threshold}
