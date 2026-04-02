import re, os
os.chdir(os.path.expanduser('~/sora_earth_ai_platform'))

# FIX 1: app/cache.py
open('app/cache.py','w').write('import time\nimport json\nimport hashlib\n\nclass SimpleCache:\n    def __init__(self):\n        self.store = {}\n    def make_key(self, prefix, payload):\n        raw = f"{prefix}:{json.dumps(payload, sort_keys=True, default=str)}"\n        return hashlib.md5(raw.encode()).hexdigest()\n    def get(self, key):\n        item = self.store.get(key)\n        if not item: return None\n        value, expires_at = item\n        if expires_at is not None and expires_at < time.time():\n            del self.store[key]\n            return None\n        return value\n    def set(self, key, value, ttl=0):\n        expires_at = time.time() + ttl if ttl > 0 else None\n        self.store[key] = (value, expires_at)\n    def stats(self): return {"items": len(self.store)}\n    def clear(self): self.store.clear()\n\n_cache = SimpleCache()\nmake_key = _cache.make_key\nget = _cache.get\nset = _cache.set\nstats = _cache.stats\nclear = _cache.clear\n')
print("1/8 cache.py")

# FIX 2: app/middleware.py
open('app/middleware.py','w').write('import time\nimport structlog\nfrom fastapi import Request\nfrom starlette.middleware.base import BaseHTTPMiddleware\n\nlogger = structlog.get_logger("sora_earth")\nSTART_TIME = time.time()\n\nMETRICS = {\n    "requests_total": 0, "predictions_total": 0, "errors_total": 0,\n    "avg_response_time_ms": 0.0, "total_response_time_ms": 0.0,\n    "evaluations_total": 0, "evaluations_avg_score": 0.0,\n    "uptime_seconds": 0.0, "requests_by_endpoint": {}, "requests_by_status": {},\n    "counters": {"http_requests_total": 0, "http_request": 0},\n}\n\nclass MetricsMiddleware(BaseHTTPMiddleware):\n    async def dispatch(self, request, call_next):\n        start = time.time()\n        METRICS["requests_total"] += 1\n        METRICS["counters"]["http_requests_total"] += 1\n        METRICS["counters"]["http_request"] += 1\n        path = request.url.path\n        METRICS["requests_by_endpoint"][path] = METRICS["requests_by_endpoint"].get(path, 0) + 1\n        try:\n            response = await call_next(request)\n        except Exception:\n            METRICS["errors_total"] += 1\n            raise\n        sk = str(response.status_code)\n        METRICS["requests_by_status"][sk] = METRICS["requests_by_status"].get(sk, 0) + 1\n        if response.status_code >= 400: METRICS["errors_total"] += 1\n        elapsed_ms = (time.time() - start) * 1000\n        METRICS["total_response_time_ms"] += elapsed_ms\n        METRICS["avg_response_time_ms"] = round(METRICS["total_response_time_ms"] / METRICS["requests_total"], 2)\n        METRICS["uptime_seconds"] = round(time.time() - START_TIME, 2)\n        logger.info(f"{request.method} {path} {response.status_code} {elapsed_ms:.2f}ms")\n        return response\n')
print("2/8 middleware.py")

# FIX 3: app/api/predict.py
os.makedirs('app/api', exist_ok=True)
if not os.path.exists('app/api/__init__.py'): open('app/api/__init__.py','w').write('')
open('app/api/predict.py','w').write('from typing import List\nfrom fastapi import APIRouter, HTTPException\nfrom fastapi.responses import StreamingResponse\nfrom pydantic import BaseModel\nimport csv, io, os\nimport numpy as np\nimport torch\nfrom app.schemas import ProjectInput as Project\nfrom app.validators import ProjectInput as LegacyProjectInput\nfrom app.mlflow_tracking import log_prediction\nfrom app.middleware import METRICS\n\nrouter = APIRouter()\n\nclass CompareRequest(BaseModel):\n    projects: List[Project]\n\ndef _to_legacy(p):\n    return LegacyProjectInput(budget=p.budget, co2_reduction=p.co2_reduction, social_impact=p.social_impact, duration_months=p.duration_months)\n\ndef _nn_forward(nn_model, feats):\n    x = torch.tensor(feats.values, dtype=torch.float32)\n    return float(nn_model(x).detach().numpy()[0][0])\n\n@router.post("/predict")\ndef predict_project(project: Project):\n    from app.main import rf_model, best_threshold, make_features\n    feats = make_features(_to_legacy(project))\n    proba = float(rf_model.predict_proba(feats)[0][1])\n    prediction = int(proba >= best_threshold)\n    result = {"prediction": prediction, "probability": round(proba*100,2), "model": "RandomForest", "threshold": best_threshold}\n    log_prediction("predict", project, result)\n    METRICS["predictions_total"] = METRICS.get("predictions_total",0)+1\n    return result\n\n@router.post("/predict/neural")\ndef predict_neural(project: Project):\n    from app.main import nn_model, best_threshold, make_features\n    feats = make_features(_to_legacy(project))\n    p = _nn_forward(nn_model, feats)\n    prediction = int(p >= best_threshold)\n    result = {"prediction": prediction, "probability": round(p*100,2), "model": "NeuralNet", "threshold": best_threshold}\n    log_prediction("predict/neural", project, result)\n    METRICS["predictions_total"] = METRICS.get("predictions_total",0)+1\n    return result\n\n@router.post("/predict/stacking")\ndef predict_stacking(project: Project):\n    from app.main import rf_model, xgb_model, nn_model, ensemble_model, best_threshold, make_features\n    feats = make_features(_to_legacy(project))\n    rf_p = float(rf_model.predict_proba(feats)[0][1])\n    xgb_p = float(xgb_model.predict_proba(feats)[0][1])\n    nn_p = _nn_forward(nn_model, feats)\n    base_preds = np.array([[rf_p, xgb_p, nn_p]])\n    ens_p = float(ensemble_model.predict_proba(base_preds)[0][1])\n    prediction = int(ens_p >= best_threshold)\n    result = {"prediction": prediction, "probability": round(ens_p*100,2), "base_models": {"rf": round(rf_p*100,2), "xgb": round(xgb_p*100,2), "nn": round(nn_p*100,2)}, "threshold": best_threshold, "model": "StackingEnsemble"}\n    log_prediction("predict/stacking", project, result)\n    METRICS["predictions_total"] = METRICS.get("predictions_total",0)+1\n    return result\n\n@router.post("/predict/compare")\ndef predict_compare(req: CompareRequest):\n    from app.main import rf_model, xgb_model, nn_model, ensemble_model, best_threshold, make_features\n    results = []\n    for p in req.projects:\n        feats = make_features(_to_legacy(p))\n        rf_p = float(rf_model.predict_proba(feats)[0][1])\n        xgb_p = float(xgb_model.predict_proba(feats)[0][1])\n        nn_p = _nn_forward(nn_model, feats)\n        base_preds = np.array([[rf_p, xgb_p, nn_p]])\n        ens_p = float(ensemble_model.predict_proba(base_preds)[0][1])\n        prediction = int(ens_p >= best_threshold)\n        results.append({"name": p.name, "prediction": prediction, "probability": round(ens_p*100,2), "base_models": {"rf": round(rf_p*100,2), "xgb": round(xgb_p*100,2), "nn": round(nn_p*100,2)}})\n    results_sorted = sorted(results, key=lambda x: x["probability"], reverse=True)\n    METRICS["predictions_total"] = METRICS.get("predictions_total",0)+len(req.projects)\n    return {"projects": results_sorted}\n\n@router.post("/shap")\ndef shap_explain(project: Project):\n    from app.main import explainer_shap, make_features\n    feats = make_features(_to_legacy(project))\n    shap_values = explainer_shap.shap_values(feats)\n    vals = shap_values[1][0].tolist() if isinstance(shap_values, list) else shap_values[0].tolist()\n    feature_names = list(feats.columns)\n    return {"shap_values": dict(zip(feature_names, vals)), "feature_names": feature_names}\n\n@router.get("/predictions/history")\ndef predictions_history():\n    from app.main import PRED_LOG\n    if not PRED_LOG: raise HTTPException(status_code=500, detail="Prediction log path not configured")\n    if not os.path.exists(PRED_LOG): return []\n    with open(PRED_LOG,"r") as f: rows = list(csv.DictReader(f))\n    return rows\n\n@router.get("/predictions/export/csv")\ndef export_predictions_csv():\n    from app.main import PRED_LOG\n    if not PRED_LOG or not os.path.exists(PRED_LOG): raise HTTPException(status_code=404, detail="No prediction log found")\n    with open(PRED_LOG,"r") as f: content = f.read()\n    return StreamingResponse(io.BytesIO(content.encode()), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=sora_predictions_log.csv"})\n')
print("3/8 predict.py")

# FIX 4: app/api/analytics.py
open('app/api/analytics.py','w').write('import random\nfrom fastapi import APIRouter\nfrom app.schemas import ProjectInput as Project\nfrom app.country_benchmarks import BENCHMARKS, GLOBAL_AVG\n\nrouter = APIRouter()\n\n@router.get("/analytics/country-benchmark/{country}")\ndef country_benchmark(country: str):\n    bench = BENCHMARKS.get(country, GLOBAL_AVG)\n    return {"country": country if country in BENCHMARKS else "Global Average", "co2_per_capita": bench["co2_per_capita"], "renewable_share": bench["renewable_share"], "esg_rank": bench["esg_rank"], "hdi": bench["hdi"]}\n\n@router.get("/analytics/country-ranking")\ndef country_ranking():\n    ranking = [{"country": c, "esg_rank": d["esg_rank"], "co2_per_capita": d["co2_per_capita"], "renewable_share": d["renewable_share"], "hdi": d["hdi"]} for c, d in BENCHMARKS.items()]\n    ranking.sort(key=lambda x: x["esg_rank"], reverse=True)\n    return ranking\n\n@router.post("/analytics/monte-carlo")\ndef monte_carlo(project: Project, n: int = 1000):\n    from app.main import calculate_esg, COUNTRIES\n    cdata = COUNTRIES.get(project.region or "Germany", {"region": "Europe"})\n    region_name = cdata.get("region", "Europe")\n    base = calculate_esg(project, region_name)\n    scores = []\n    for _ in range(n):\n        d = project.model_dump()\n        d["budget"] = d["budget"] * random.uniform(0.8, 1.2)\n        d["co2_reduction"] = max(0, d["co2_reduction"] + random.uniform(-20, 20))\n        d["social_impact"] = max(0, min(10, d["social_impact"] + random.uniform(-1, 1)))\n        d["duration_months"] = max(1, int(d["duration_months"] + random.randint(-3, 3)))\n        mod = Project(**d)\n        r = calculate_esg(mod, region_name)\n        scores.append(r["total_score"])\n    scores.sort()\n    return {"base_score": base["total_score"], "simulations": n, "mean": round(sum(scores)/len(scores),2), "median": round(scores[len(scores)//2],2), "p5": round(scores[int(n*0.05)],2), "p95": round(scores[int(n*0.95)],2), "min": round(min(scores),2), "max": round(max(scores),2)}\n\n@router.post("/analytics/model-compare")\ndef model_compare(project: Project):\n    from app.main import rf_model, xgb_model, nn_model, ensemble_model, best_threshold, make_features\n    from app.validators import ProjectInput as LegacyProjectInput\n    import torch, numpy as np\n    feats = make_features(LegacyProjectInput(budget=project.budget, co2_reduction=project.co2_reduction, social_impact=project.social_impact, duration_months=project.duration_months))\n    rf_p = float(rf_model.predict_proba(feats)[0][1])\n    xgb_p = float(xgb_model.predict_proba(feats)[0][1])\n    x = torch.tensor(feats.values, dtype=torch.float32)\n    nn_p = float(nn_model(x).detach().numpy()[0][0])\n    base_preds = np.array([[rf_p, xgb_p, nn_p]])\n    ens_p = float(ensemble_model.predict_proba(base_preds)[0][1])\n    models = {"RandomForest": {"probability": round(rf_p*100,2), "prediction": int(rf_p>=best_threshold)}, "XGBoost": {"probability": round(xgb_p*100,2), "prediction": int(xgb_p>=best_threshold)}, "NeuralNet": {"probability": round(nn_p*100,2), "prediction": int(nn_p>=best_threshold)}, "StackingEnsemble": {"probability": round(ens_p*100,2), "prediction": int(ens_p>=best_threshold)}}\n    return {"models": models, "threshold": best_threshold}\n')
print("4/8 analytics.py")

# FIX 5: typo in main.py
with open('app/main.py','r') as f: c = f.read()
lines = c.split('\n')
for i, line in enumerate(lines):
    if 'uccessprob' in line:
        lines[i] = '    risk_level = "Low" if total >= 75 and success_prob >= 70 else "Medium" if total >= 40 else "High"'
c = '\n'.join(lines)
assert 'uccessprob' not in c
with open('app/main.py','w') as f: f.write(c)
print("5/8 typo fixed")

# FIX 6: add routers
with open('app/main.py','r') as f: c = f.read()
if 'from app.api import predict' not in c:
    lines = c.split('\n')
    idx = max((i for i,l in enumerate(lines) if l.startswith('from app')), default=0)
    lines.insert(idx+1, 'from app.api import predict as predict_api')
    c = '\n'.join(lines)
if 'from app.api import analytics' not in c:
    lines = c.split('\n')
    idx = max((i for i,l in enumerate(lines) if l.startswith('from app')), default=0)
    lines.insert(idx+1, 'from app.api import analytics as analytics_api')
    c = '\n'.join(lines)
if 'predict_api.router' not in c:
    lines = c.split('\n')
    for i, l in enumerate(lines):
        if 'include_router' in l and 'evaluate' in l:
            lines.insert(i+1, 'app.include_router(predict_api.router)')
            break
    c = '\n'.join(lines)
if 'analytics_api.router' not in c:
    lines = c.split('\n')
    for i, l in enumerate(lines):
        if 'predict_api.router' in l:
            lines.insert(i+1, 'app.include_router(analytics_api.router)')
            break
    c = '\n'.join(lines)
with open('app/main.py','w') as f: f.write(c)
print("6/8 routers added")

# FIX 7: test predict/compare
with open('tests/test_api.py','r') as f: c = f.read()
c = re.sub(r'client\.post\(\s*["\']\/predict\/compare["\']\s*,\s*json\s*=\s*PROJECT\s*\)', 'client.post("/predict/compare", json={"projects": [PROJECT]})', c)
with open('tests/test_api.py','w') as f: f.write(c)
print("7/8 test fixed")

# FIX 8: prometheus uptime
with open('app/main.py','r') as f: c = f.read()
# check if prometheus endpoint exists and add uptime_seconds if missing
if 'metrics/prometheus' in c and 'uptime_seconds' not in c.split('prometheus')[1][:800] if 'prometheus' in c else False:
    c = c.replace('sora_errors_total', 'sora_uptime_seconds {METRICS["uptime_seconds"]}\\nsora_errors_total')
    with open('app/main.py','w') as f: f.write(c)
print("8/8 done")
print("\n===== ALL FIXES APPLIED =====")
print("Run:\n  source venv/bin/activate\n  python3 -m pytest tests/ -q --tb=short --disable-warnings")
