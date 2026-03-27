from app.auth import Token, LoginRequest, UserInfo, verify_password, create_access_token, get_current_user, require_auth, require_admin, USERS_DB
from app.country_benchmarks import BENCHMARKS, GLOBAL_AVG
from functools import lru_cache
from fastapi import FastAPI, Depends, HTTPException, Request
from prometheus_fastapi_instrumentator import Instrumentator
from app.cache import cache
from app.rate_limit import rate_limiter
from app.auth import get_api_key, require_api_key, require_admin_apikey, api_key_header
from app.metrics import metrics
from app.batch import BatchRequest, BatchResult, batch_history, generate_batch_id
from app.websocket import manager, WebSocket, WebSocketDisconnect
from app.drift_detection import drift_detector
from app.mlflow_tracking import log_prediction, log_evaluation, get_experiment_stats
from app.rate_limit import limiter, rate_limit_handler, SlowAPIMiddleware, RateLimitExceeded
from app.logging_config import setup_logging
import sentry_sdk
from app.middleware import MetricsMiddleware, METRICS
from app.validators import ProjectInput
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, PlainTextResponse
from typing import List
import pickle, numpy as np, math, os, io, csv, json, logging, shap
import pandas as pd
from datetime import datetime
import time
import torch
import torch.nn as tnn

from app.schemas import ProjectInput as Project, GHGInput

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sora")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
DB_PATH = os.path.join(ROOT_DIR, "data", "history.db")
PRED_LOG = os.path.join(ROOT_DIR, "data", "predictions_log.csv")
import sqlite3

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS evaluations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, budget REAL, co2_reduction REAL, social_impact REAL,
        duration_months INTEGER, total_score REAL, environment_score REAL,
        social_score REAL, economic_score REAL, success_probability REAL,
        recommendation TEXT, risk_level TEXT, created_at TEXT,
        region TEXT DEFAULT 'Europe', lat REAL DEFAULT 50.0, lon REAL DEFAULT 10.0)""")
    conn.commit()
    conn.close()

def log_prediction(endpoint, input_data, result):
    file_exists = os.path.exists(PRED_LOG)
    with open(PRED_LOG, "a", newline="") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(["timestamp","endpoint","budget","co2_reduction","social_impact","duration_months","prediction","probability"])
        w.writerow([datetime.datetime.now().isoformat(), endpoint,
                    input_data.budget, input_data.co2_reduction, input_data.social_impact,
                    input_data.duration_months, result.get("prediction",""), result.get("probability","")])

app = FastAPI(
    swagger_ui_parameters={"defaultModelsExpandDepth": -1, "docExpansion": "list", "filter": True},
    redoc_url="/redoc",title="SORA.Earth AI Platform", version="2.0.0")

# ============ SENTRY ERROR TRACKING ============
import os
if os.getenv("SENTRY_DSN"):
    sentry_sdk.init(dsn=os.getenv("SENTRY_DSN"), traces_sample_rate=0.1, environment=os.getenv("SORA_ENV", "development"))

# ============ STRUCTURED LOGGING ============
logger = setup_logging()

# ============ RATE LIMITING ============
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(MetricsMiddleware)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

with open(os.path.join(ROOT_DIR,"models","scaler.pkl"),"rb") as f: scaler=pickle.load(f)
with open(os.path.join(ROOT_DIR,"models","model.pkl"),"rb") as f: rf_model=pickle.load(f)
with open(os.path.join(ROOT_DIR,"models","meta.json"),"r") as f: model_meta=json.load(f)
with open(os.path.join(ROOT_DIR,"models","xgb_model.pkl"),"rb") as f: xgb_model=pickle.load(f)
with open(os.path.join(ROOT_DIR,"models","metrics.json"),"r") as f: model_metrics=json.load(f)
with open(os.path.join(ROOT_DIR,"models","stacking_meta.pkl"),"rb") as f: stacking_meta=pickle.load(f)
with open(os.path.join(ROOT_DIR,"models","best_threshold.pkl"),"rb") as f: best_threshold=pickle.load(f)["threshold"]

ENS_PATH=os.path.join(ROOT_DIR,"models","ensemble_model.pkl")
ensemble_model=None
if os.path.exists(ENS_PATH):
    with open(ENS_PATH,"rb") as f: ensemble_model=pickle.load(f)

class SoraNet(tnn.Module):
    def __init__(self):
        super().__init__()
        self.net=tnn.Sequential(tnn.Linear(7,64),tnn.ReLU(),tnn.BatchNorm1d(64),tnn.Dropout(0.3),
            tnn.Linear(64,32),tnn.ReLU(),tnn.BatchNorm1d(32),tnn.Dropout(0.2),
            tnn.Linear(32,16),tnn.ReLU(),tnn.Linear(16,1),tnn.Sigmoid())
    def forward(self,x): return self.net(x)

nn_model=SoraNet()
NN_PATH=os.path.join(ROOT_DIR,"models","pytorch_mlp.pth")
if os.path.exists(NN_PATH):
    nn_model.load_state_dict(torch.load(NN_PATH,map_location="cpu"))
    nn_model.eval()

init_db()
logger.info("SORA.Earth AI Platform started")
explainer_shap = shap.TreeExplainer(rf_model)

FEATURE_COLS=["budget","co2_reduction","social_impact","duration_months","budget_per_month","co2_per_dollar","efficiency_score"]

def make_features(data):
    budget_per_month = data.budget / max(data.duration_months, 1)
    co2_per_dollar = data.co2_reduction / max(data.budget, 1) * 1000
    efficiency_score = (data.co2_reduction * data.social_impact) / max(data.duration_months, 1)
    df = pd.DataFrame([[data.budget, data.co2_reduction, data.social_impact, data.duration_months, budget_per_month, co2_per_dollar, efficiency_score]], columns=FEATURE_COLS)
    return scaler.transform(df)

COUNTRIES = {
    "Afghanistan":{"lat":33.9,"lon":67.7,"region":"Asia"},"Albania":{"lat":41.2,"lon":20.2,"region":"Europe"},
    "Algeria":{"lat":28.0,"lon":1.7,"region":"Africa"},"Argentina":{"lat":-38.4,"lon":-63.6,"region":"South America"},
    "Australia":{"lat":-25.3,"lon":133.8,"region":"Oceania"},"Austria":{"lat":47.5,"lon":14.6,"region":"Europe"},
    "Brazil":{"lat":-14.2,"lon":-51.9,"region":"South America"},"Canada":{"lat":56.1,"lon":-106.3,"region":"North America"},
    "China":{"lat":35.9,"lon":104.2,"region":"Asia"},"France":{"lat":46.2,"lon":2.2,"region":"Europe"},
    "Germany":{"lat":51.2,"lon":10.5,"region":"Europe"},"India":{"lat":20.6,"lon":79.0,"region":"Asia"},
    "Italy":{"lat":41.9,"lon":12.6,"region":"Europe"},"Japan":{"lat":36.2,"lon":138.3,"region":"Asia"},
    "Mexico":{"lat":23.6,"lon":-102.6,"region":"North America"},"Nigeria":{"lat":9.1,"lon":8.7,"region":"Africa"},
    "Russia":{"lat":61.5,"lon":105.3,"region":"Europe"},"South Africa":{"lat":-30.6,"lon":22.9,"region":"Africa"},
    "Spain":{"lat":40.5,"lon":-3.7,"region":"Europe"},"United Kingdom":{"lat":55.4,"lon":-3.4,"region":"Europe"},
    "United States":{"lat":37.1,"lon":-95.7,"region":"North America"},
}

REGIONAL_FACTORS = {
    "Europe":{"env_mult":1.1,"soc_mult":1.05,"eco_mult":1.0,"renewable_bonus":0.05},
    "North America":{"env_mult":1.0,"soc_mult":1.0,"eco_mult":1.1,"renewable_bonus":0.03},
    "South America":{"env_mult":1.15,"soc_mult":0.9,"eco_mult":0.85,"renewable_bonus":0.08},
    "Asia":{"env_mult":0.9,"soc_mult":0.95,"eco_mult":1.05,"renewable_bonus":0.02},
    "Africa":{"env_mult":1.2,"soc_mult":0.85,"eco_mult":0.75,"renewable_bonus":0.1},
    "Oceania":{"env_mult":1.05,"soc_mult":1.1,"eco_mult":1.0,"renewable_bonus":0.06},
    "Middle East":{"env_mult":0.85,"soc_mult":0.9,"eco_mult":1.15,"renewable_bonus":0.01},
}

def calculate_esg(project, region_name='Europe'):
    rf = REGIONAL_FACTORS.get(region_name, REGIONAL_FACTORS["Europe"])
    score_env = min(project.co2_reduction / 100.0 * rf["env_mult"] + rf["renewable_bonus"], 1.0)
    score_soc = min(project.social_impact / 10.0 * rf["soc_mult"], 1.0)
    score_eco = min(1.0 / (1.0 + math.exp(-0.00005 * (project.budget - 50000))) * rf["eco_mult"], 1.0)
    duration_factor = 0.9 if project.duration_months > 48 else (0.95 if project.duration_months > 36 else 1.0)
    total = round((score_env*0.4 + score_soc*0.3 + score_eco*0.3) * duration_factor * 100, 2)
    total = min(total, 100.0)
    features_scaled = make_features(project)
    success_prob = round(float(rf_model.predict_proba(features_scaled)[0][1]) * 100, 2)
    recommendations = []
    if score_env < 0.7:
        target_co2 = min(int(project.co2_reduction + (0.7 - score_env) * 100), 100)
        recommendations.append(f"Increase CO2 reduction from {project.co2_reduction}% to {target_co2}%+ to reach Strong environmental rating")
    if score_soc < 0.7:
        target_si = min(int(project.social_impact + (0.7 - score_soc) * 10) + 1, 10)
        recommendations.append(f"Boost social impact score from {project.social_impact} to {target_si}+ (add community engagement, job creation programs)")
    if score_eco < 0.5:
        recommendations.append(f"Budget of ${project.budget:,.0f} is below optimal. Consider $75,000+ for stronger economic score")
    elif score_eco < 0.7:
        recommendations.append(f"Budget is moderate. Increasing to $120,000+ would significantly improve economic rating")
    if project.duration_months > 36:
        recommendations.append(f"Duration of {project.duration_months} months applies a penalty. Consider splitting into phases under 36 months")
    elif project.duration_months < 6:
        recommendations.append("Very short timeline may limit impact. Consider extending to 6-12 months")
    if total < 50:
        recommendations.append("⚠️ High risk: focus on CO2 reduction and social impact as priority improvements")
    elif total >= 75 and success_prob >= 70:
        recommendations.append("[OK] Excellent ESG profile — r green bond certification")
    if not recommendations:
        recommendations.append("Strong project across all ESG dimensions — consider scaling up")
    risk_level = "Low" if total >= 75 and success_prob >= 70 else ("Medium" if total >= 50 and success_prob >= 40 else "High")
    return {"total_score":total,"environment_score":round(score_env*100,1),"social_score":round(score_soc*100,1),
        "economic_score":round(score_eco*100,1),"success_probability":success_prob,"recommendations":recommendations,
        "risk_level":risk_level,"esg_weights":{"environment":0.4,"social":0.3,"economic":0.3}}

# ---- ENDPOINTS ----



# ============ AUTHENTICATION ============
@app.post("/auth/login", response_model=Token, tags=["auth"])
def login(req: LoginRequest):
    user = USERS_DB.get(req.username)
    if not user or not verify_password(req.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": user["username"], "role": user["role"]})
    return Token(access_token=token, token_type="bearer", expires_in=3600)

@app.get("/auth/me", tags=["auth"])
def get_me(user: UserInfo = Depends(require_auth)):
    return {"username": user.username, "role": user.role}

@app.get("/admin/users", tags=["admin"])
def list_users(user: UserInfo = Depends(require_admin)):
    return [{"username": u, "role": d["role"]} for u, d in USERS_DB.items()]






# ============ METRICS & RATE LIMITING ============
@app.get("/metrics", tags=["monitoring"])
def get_metrics():
    return metrics.summary()

@app.get("/metrics/prometheus", tags=["monitoring"])
def prometheus_metrics():
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(metrics.prometheus_format(), media_type="text/plain")

@app.get("/rate-limit/status", tags=["monitoring"])
def rate_limit_status(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    return rate_limiter.get_usage(client_ip)

@app.get("/auth/verify", tags=["auth"])
def verify_key(user=Depends(require_api_key)):
    return {"authenticated": True, "user": user["name"], "role": user["role"]}

@app.get("/admin/stats", tags=["admin"])
def admin_stats(user=Depends(require_admin_apikey)):
    return {"metrics": metrics.summary(), "authenticated_as": user["name"]}

# ============ BATCH API ============
@app.post("/batch/evaluate", tags=["batch"])
def batch_evaluate(req: BatchRequest):
    batch_id = generate_batch_id()
    start = time.time()
    results = []
    success = 0
    fail = 0
    for p in req.projects:
        try:
            project = Project(**{k: v for k, v in p.items() if k in Project.__fields__})
            cdata = COUNTRIES.get(project.region or "Germany", {"region":"Europe","lat":50.0,"lon":10.0})
            region_name = cdata.get("region","Europe")
            result = calculate_esg(project, region_name)
            result["project_name"] = project.name
            result["status"] = "success"
            results.append(result)
            success += 1
        except Exception as e:
            results.append({"project_name": p.get("name","unknown"), "status": "error", "error": str(e)})
            fail += 1
    elapsed = round((time.time() - start) * 1000, 2)
    batch_result = {"batch_id": batch_id, "total": len(req.projects), "successful": success, "failed": fail, "results": results, "processing_time_ms": elapsed}
    batch_history[batch_id] = batch_result
    return batch_result

@app.get("/batch/{batch_id}", tags=["batch"])
def get_batch(batch_id: str):
    if batch_id not in batch_history:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch_history[batch_id]

@app.get("/batch", tags=["batch"])
def list_batches():
    return [{"batch_id": k, "total": v["total"], "successful": v["successful"]} for k, v in batch_history.items()]

# ============ WEBSOCKET ============
@app.websocket("/ws/live")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            data = await ws.receive_text()
            await ws.send_json({"echo": data, "connections": manager.count})
    except WebSocketDisconnect:
        manager.disconnect(ws)

@app.get("/ws/status", tags=["websocket"])
def ws_status():
    return {"active_connections": manager.count}

# ============ CACHE MANAGEMENT ============
@app.get("/cache/stats", tags=["cache"])
def cache_stats():
    return cache.stats()

@app.post("/cache/clear", tags=["cache"])
def clear_cache():
    cache.clear()
    return {"status": "cache cleared"}

# ============ DATA DRIFT DETECTION ============
@app.get("/mlops/drift", tags=["mlops"])
def check_drift():
    return drift_detector.check_drift()

@app.get("/mlops/health", tags=["mlops"])
def mlops_health():
    drift = drift_detector.check_drift()
    return {
        "model_status": "healthy",
        "drift_status": drift["status"],
        "observations_tracked": drift["observations"],
        "monitoring": {
            "prometheus": "/metrics",
            "mlflow": "/mlflow/stats",
            "drift": "/mlops/drift"
        }
    }

# ============ MLFLOW TRACKING ============
@app.get("/mlflow/stats", tags=["mlflow"])
def mlflow_stats():
    return get_experiment_stats()

# ============ PROMETHEUS METRICS ============
Instrumentator().instrument(app).expose(app)

@app.get("/")
def read_root():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))

@app.get("/health")
def health():
    return {"status":"ok","models_loaded":True,"version":"2.0.0"}

@app.post("/evaluate")
def evaluate_project(project: Project):
    cache_key = cache._make_key("eval", project.dict())
    cached = cache.get(cache_key)
    if cached:
        return cached
    cdata = COUNTRIES.get(project.region or "Germany", {"region":"Europe","lat":50.0,"lon":10.0})
    region_name = cdata.get("region","Europe")
    result = calculate_esg(project, region_name)
    lat = cdata["lat"] + (hash(project.name) % 10 - 5) * 0.3
    lon = cdata["lon"] + (hash(project.name) % 10 - 5) * 0.3
    conn = get_db()
    conn.execute("INSERT INTO evaluations (name,budget,co2_reduction,social_impact,duration_months,total_score,environment_score,social_score,economic_score,success_probability,recommendation,risk_level,created_at,region,lat,lon) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (project.name,project.budget,project.co2_reduction,project.social_impact,project.duration_months,result["total_score"],result["environment_score"],result["social_score"],result["economic_score"],result["success_probability"],"; ".join([_sanitize_pdf(r) for r in result["recommendations"]]),result["risk_level"],datetime.datetime.now().isoformat(),region_name,lat,lon))
    conn.commit(); conn.close()
    result["region"]=region_name; result["lat"]=lat; result["lon"]=lon
    log_evaluation(project.name, result, result["risk_level"])
    drift_detector.add_observation({"budget": project.budget, "co2_reduction": project.co2_reduction, "social_impact": project.social_impact, "duration_months": project.duration_months})

    country_name = project.region or "Germany"
    bench = BENCHMARKS.get(country_name, GLOBAL_AVG)
    result["country_benchmark"] = {
        "country": country_name if country_name in BENCHMARKS else "Global Average",
        "co2_per_capita": bench["co2_per_capita"],
        "renewable_share": bench["renewable_share"],
        "esg_rank": bench["esg_rank"],
        "hdi": bench["hdi"],
        "project_vs_country": {"esg_score_diff": round(result["total_score"] - bench["esg_rank"], 2), "above_average": result["total_score"] > 50}
    }
    cache.set(cache_key, result, ttl=600)
    return result

@app.post("/predict/compare")
def predict_compare(data: ProjectInput):
    features_scaled = make_features(data)
    rf_prob = round(float(rf_model.predict_proba(features_scaled)[0][1])*100,2)
    xgb_prob = round(float(xgb_model.predict_proba(features_scaled)[0][1])*100,2)
    return {"RandomForest":{"probability":rf_prob},"XGBoost":{"probability":xgb_prob},
        "agreement":abs(rf_prob-xgb_prob)<15,"metrics":model_metrics}

@app.post("/predict/neural")
def predict_neural(data: ProjectInput):
    features_scaled = make_features(data)
    with torch.no_grad():
        nn_prob = round(float(nn_model(torch.FloatTensor(features_scaled))[0][0])*100,2)
    rf_prob = round(float(rf_model.predict_proba(features_scaled)[0][1])*100,2)
    xgb_prob = round(float(xgb_model.predict_proba(features_scaled)[0][1])*100,2)
    return {"RandomForest":rf_prob,"XGBoost":xgb_prob,"PyTorch_MLP":nn_prob,
        "agreement":max(rf_prob,xgb_prob,nn_prob)-min(rf_prob,xgb_prob,nn_prob)<20}

@app.post("/predict/stacking")
def predict_stacking(data: ProjectInput):
    features_scaled = make_features(data)
    rf_prob = rf_model.predict_proba(features_scaled)[:,1]
    gb_prob = xgb_model.predict_proba(features_scaled)[:,1]
    nn_model.eval()
    with torch.no_grad():
        nn_prob = nn_model(torch.FloatTensor(features_scaled.values if hasattr(features_scaled,'values') else features_scaled)).squeeze().numpy()
    meta_features = np.column_stack([rf_prob, gb_prob, [float(nn_prob)]])
    prob = stacking_meta.predict_proba(meta_features)[:,1][0]
    prediction = int(prob >= best_threshold)
    result = {"model":"Stacking (RF+XGB+NN)","prediction":prediction,"probability":round(float(prob),4),
        "threshold":round(float(best_threshold),3),
        "individual_probs":{"random_forest":round(float(rf_prob[0]),4),"xgboost":round(float(gb_prob[0]),4),"neural_network":round(float(nn_prob),4)}}
    log_prediction("stacking", data, result)
    return result

@app.post("/explain/shap")
def explain_shap(data: ProjectInput):
    features_scaled = make_features(data)
    shap_values = explainer_shap.shap_values(features_scaled)
    if isinstance(shap_values, list): sv = shap_values[1][0]
    elif shap_values.ndim == 3: sv = shap_values[0,:,1]
    else: sv = shap_values[0]
    feature_names = ["Budget","CO2 Reduction","Social Impact","Duration","Budget/Month","CO2/Dollar","Efficiency"]
    base = explainer_shap.expected_value
    if isinstance(base,(list,np.ndarray)): base = base[1]
    return {"features":feature_names,"shap_values":[round(float(v.item() if hasattr(v,"item") else v),4) for v in sv],"base_value":round(float(base),4)}

@app.post("/predict/batch")
def predict_batch(projects: List[dict]):
    results = []
    for p in projects:
        try:
            proj = ProjectInput(**p)
            features_scaled = make_features(proj)
            rf_prob = rf_model.predict_proba(features_scaled)[:,1]
            xgb_prob = xgb_model.predict_proba(features_scaled)[:,1]
            with torch.no_grad():
                nn_prob_val = nn_model(torch.FloatTensor(features_scaled)).squeeze().item()
            meta_features = np.column_stack([rf_prob,xgb_prob,[[nn_prob_val]]])
            prob = stacking_meta.predict_proba(meta_features)[:,1][0]
            prediction = int(prob >= best_threshold)
            results.append({"input":p,"prediction":prediction,"probability":round(float(prob),4),"status":"ok"})
        except Exception as e:
            results.append({"input":p,"error":str(e),"status":"error"})
    return {"total":len(results),"success":sum(1 for r in results if r["status"]=="ok"),"results":results}

@app.get("/predictions/history")
def predictions_history(limit: int = 50):
    if not os.path.exists(PRED_LOG): return {"predictions":[],"total":0}
    df = pd.read_csv(PRED_LOG)
    return {"predictions":df.tail(limit).to_dict(orient="records"),"total":len(df)}

@app.get("/history")
def get_history():
    conn = get_db()
    rows = conn.execute("SELECT * FROM evaluations ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.delete("/history/{eval_id}")
def delete_evaluation(eval_id: int):
    conn = get_db(); conn.execute("DELETE FROM evaluations WHERE id=?",(eval_id,)); conn.commit(); conn.close()
    return {"status":"deleted"}

@app.delete("/history")
def clear_history():
    conn = get_db(); conn.execute('DELETE FROM evaluations'); conn.commit(); conn.close()
    return {"status":"cleared"}

@app.get("/export/csv")
def export_csv():
    conn = get_db()
    rows = conn.execute("SELECT name,budget,co2_reduction,social_impact,duration_months,total_score,environment_score,social_score,economic_score,success_probability,risk_level,region,created_at FROM evaluations ORDER BY created_at DESC").fetchall()
    conn.close()
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["Name","Budget","CO2 Reduction","Social Impact","Duration (months)","ESG Score","Environment","Social","Economic","Success Probability","Risk Level","Region","Date"])
    for r in rows:
        w.writerow([r["name"],r["budget"],r["co2_reduction"],r["social_impact"],r["duration_months"],r["total_score"],r["environment_score"],r["social_score"],r["economic_score"],r["success_probability"],r["risk_level"],r["region"],r["created_at"]])
    output.seek(0)
    return StreamingResponse(io.BytesIO(output.getvalue().encode()), media_type="text/csv", headers={"Content-Disposition":"attachment; filename=sora_earth_projects.csv"})

# EXPORT_MARKER
    conn = get_db(); conn.execute("DELETE FROM evaluations"); conn.commit(); conn.close()
    return {"status":"cleared"}

@app.get("/export/csv")
def export_csv():
    conn = get_db()
    rows = conn.execute("SELECT * FROM evaluations ORDER BY created_at DESC").fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id","name","budget","co2_reduction","social_impact","duration_months","total_score","environment_score","social_score","economic_score","success_probability","recommendation","risk_level","created_at","region"])
    for r in rows: writer.writerow(list(r))
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=sora_earth_report.csv"})

@app.get("/model-info")
def model_info(): return model_meta

@app.get("/model-metrics")
def get_model_metrics(): return model_metrics

@app.post("/shap")
def shap_legacy(project: Project):
    return explain_shap(ProjectInput(budget=project.budget,co2_reduction=project.co2_reduction,social_impact=project.social_impact,duration_months=project.duration_months))

@app.post("/what-if")
def what_if(project: Project):
    cdata = COUNTRIES.get(project.region or "Germany",{"region":"Europe"})
    wi_region = cdata.get("region","Europe")
    base = calculate_esg(project, wi_region)
    variations = {}
    deltas = {"budget":("budget",0.2,True),"co2_reduction":("co2_reduction",20,False),"social_impact":("social_impact",2,False),"duration_months":("duration_months",-6,False)}
    for key,(field,delta,is_pct) in deltas.items():
        d = project.model_dump()
        d[field] = d[field]*(1+delta) if is_pct else d[field]+delta
        d[field] = max(d[field],0)
        if field=="social_impact": d[field]=min(d[field],10)
        if field=="duration_months": d[field]=max(int(d[field]),1)
        mod = Project(**d)
        mr = calculate_esg(mod, wi_region)
        variations[key] = {"new_value":round(d[field],0),"new_score":mr["total_score"],"score_change":round(mr["total_score"]-base["total_score"],2),"new_probability":mr["success_probability"],"prob_change":round(mr["success_probability"]-base["success_probability"],2)}
    return {"base":base,"variations":variations}

@app.post("/ghg-calculate")
def ghg_calculate(data: GHGInput):
    scope1 = round((data.natural_gas_m3*2.0+data.diesel_liters*2.68+data.petrol_liters*2.31)/1000,2)
    scope2 = round((data.electricity_kwh*0.4)/1000,2)
    scope3 = round((data.flights_km*0.255+data.waste_kg*0.5)/1000,2)
    total = round(scope1+scope2+scope3,2)
    breakdown = {"electricity":round(data.electricity_kwh*0.4/1000,3),"natural_gas":round(data.natural_gas_m3*2.0/1000,3),"diesel":round(data.diesel_liters*2.68/1000,3),"petrol":round(data.petrol_liters*2.31/1000,3),"flights":round(data.flights_km*0.255/1000,3),"waste":round(data.waste_kg*0.5/1000,3)}
    if total<5: rating,tip="Excellent","Your carbon footprint is well below average."
    elif total<15: rating,tip="Good","Consider switching to renewable energy."
    elif total<30: rating,tip="Average","Significant improvements possible."
    else: rating,tip="High","Urgent action needed."
    return {"total_tons_co2":total,"scope1":scope1,"scope2":scope2,"scope3":scope3,"breakdown":breakdown,"rating":rating,"tip":tip}

@app.get("/trends")
def trends():
    conn = get_db()
    rows = conn.execute("SELECT total_score,success_probability,created_at FROM evaluations ORDER BY created_at ASC").fetchall()
    conn.close()
    return [{"score":r["total_score"],"prob":r["success_probability"],"date":r["created_at"][:16].replace("T"," ")} for r in rows]

@app.get("/regions")
def regions(): return list(REGIONAL_FACTORS.keys())

@app.get("/countries")
def countries_list(): return {k:v["region"] for k,v in COUNTRIES.items()}

@app.get("/metrics")
async def get_metrics(): return METRICS

@app.get("/system/metrics")
async def get_system_metrics(): return METRICS

@app.get("/metrics/prometheus")
async def prometheus_metrics():
    m = METRICS
    lines = [f'sora_requests_total {m["requests_total"]}',f'sora_predictions_total {m["predictions_total"]}',f'sora_errors_total {m["errors_total"]}',f'sora_avg_response_time_ms {m["avg_response_time_ms"]}']
    for ep,count in m["requests_by_endpoint"].items(): lines.append(f'sora_requests_by_endpoint{{path="{ep}"}} {count}')
    for st,count in m["requests_by_status"].items(): lines.append(f'sora_requests_by_status{{status="{st}"}} {count}')
    return PlainTextResponse("\n".join(lines))

@app.post("/predict-nn")
def predict_nn_legacy(project: Project):
    data = ProjectInput(budget=project.budget, co2_reduction=project.co2_reduction, social_impact=project.social_impact, duration_months=project.duration_months)
    return predict_neural(data)

@app.post("/evaluate-compare")
def evaluate_compare_legacy(project: Project):
    data = ProjectInput(budget=project.budget, co2_reduction=project.co2_reduction, social_impact=project.social_impact, duration_months=project.duration_months)
    return predict_compare(data)


@app.get("/analytics/country-benchmark/{country}")
def country_benchmark(country: str):
    bench = BENCHMARKS.get(country, GLOBAL_AVG)
    return {
        "country": country,
        "benchmarks": bench,
        "global_average": GLOBAL_AVG,
        "comparison": {
            k: round(bench[k] - GLOBAL_AVG[k], 2) for k in GLOBAL_AVG
        }
    }

@app.get("/analytics/country-ranking")
def country_ranking():
    ranked = sorted(BENCHMARKS.items(), key=lambda x: x[1]["esg_rank"])
    return [{"country": k, **v} for k, v in ranked]

# ============ CLUSTERING ============
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

@app.get("/analytics/clusters")
def cluster_projects(n_clusters: int = 3):
    db = get_db()
    rows = db.execute("SELECT * FROM evaluations").fetchall()
    if len(rows) < n_clusters:
        return {"error": "Not enough projects", "clusters": []}
    data = []
    projects = []
    for r in rows:
        d = dict(r)
        projects.append(d)
        data.append([d.get("total_score",0), d.get("environment_score",0),
                      d.get("social_score",0), d.get("economic_score",0),
                      float(d.get("success_probability",0) or 0)])
    import numpy as np
    X = StandardScaler().fit_transform(np.array(data))
    km = KMeans(n_clusters=min(n_clusters, len(rows)), random_state=42, n_init=10).fit(X)
    for i, p in enumerate(projects):
        p["cluster"] = int(km.labels_[i])
    centers = km.cluster_centers_.tolist()
    return {"projects": projects, "centers": centers, "n_clusters": len(set(km.labels_))}

# ============ PDF REPORT ============
from fpdf import FPDF
from fastapi.responses import FileResponse
import tempfile, datetime

def _sanitize_pdf(text):
    import re
    return str(text).encode('ascii', 'ignore').decode('ascii').strip()

def sanitize_text(text: str) -> str:
    replacements = {
        "\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'",
        "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u2022": "*",
        "\u00b7": "*", "\u2212": "-", "\u00a0": " ",
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text.encode("latin-1", errors="replace").decode("latin-1")

@app.post("/report/pdf")
def generate_pdf_report(project: Project):
    esg = calculate_esg(project, project.region)
    feats = make_features(ProjectInput(budget=project.budget, co2_reduction=project.co2_reduction,
                                        social_impact=project.social_impact, duration_months=project.duration_months))
    prob = float(ensemble_model.predict_proba(feats)[0][1])
    prediction = int(prob >= best_threshold)
    risk = "Low" if esg["total_score"]>=70 else "Medium" if esg["total_score"]>=40 else "High"

    pdf = FPDF()
    _orig_normalize = pdf.normalize_text
    def _safe_normalize(txt):
        return _orig_normalize(_sanitize_pdf(txt))
    pdf.normalize_text = _safe_normalize
    pdf.add_page()
    pdf.set_font("Helvetica","B",22)
    pdf.cell(0,15,"SORA.Earth - Project ESG Report",ln=True,align="C")
    pdf.set_font("Helvetica","",10)
    pdf.cell(0,8,f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",ln=True,align="C")
    pdf.ln(10)

    pdf.set_font("Helvetica","B",14)
    pdf.cell(0,10,"Project Overview",ln=True)
    pdf.set_font("Helvetica","",11)
    info = [("Name", project.name), ("Budget", f"${project.budget:,.0f}"),
            ("CO2 Reduction", f"{project.co2_reduction} tons/year"),
            ("Social Impact", f"{project.social_impact}/10"),
            ("Duration", f"{project.duration_months} months"), ("Country", project.region)]
    for k,v in info:
        pdf.cell(60,8,k+":",0)
        pdf.cell(0,8,str(v),ln=True)
    pdf.ln(6)

    pdf.set_font("Helvetica","B",14)
    pdf.cell(0,10,"ESG Assessment",ln=True)
    pdf.set_font("Helvetica","",11)
    scores = [("Total ESG Score", f"{esg['total_score']}/100"),
              ("Environment", f"{esg['environment_score']}/100"),
              ("Social", f"{esg['social_score']}/100"),
              ("Economic", f"{esg['economic_score']}/100"),
              ("Risk Level", risk),
              ("ML Success Probability", f"{prob*100:.2f}%"),
              ("Prediction", "Success" if prediction else "Fail")]
    for k,v in scores:
        pdf.cell(60,8,k+":",0)
        pdf.cell(0,8,str(v),ln=True)
    pdf.ln(6)

    if esg.get("recommendations"):
        pdf.set_font("Helvetica","B",14)
        pdf.cell(0,10,"Recommendations",ln=True)
        pdf.set_font("Helvetica","",11)
        for i,r in enumerate(esg["recommendations"],1):
            pdf.set_x(10)
            pdf.multi_cell(0,7,_sanitize_pdf(f"{i}. {r}"))
    pdf.ln(6)

    pdf.set_font("Helvetica","I",9)
    pdf.cell(0,8,"This report was generated by SORA.Earth AI Platform v2.0",ln=True,align="C")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    pdf.output(tmp.name)
    return FileResponse(tmp.name, media_type="application/pdf",
                        filename=f"SORA_Earth_{project.name.replace(' ','_')}_Report.pdf")


@app.get("/analytics/country-benchmark/{country}")
def country_benchmark(country: str):
    bench = BENCHMARKS.get(country, GLOBAL_AVG)
    return {
        "country": country,
        "benchmarks": bench,
        "global_average": GLOBAL_AVG,
        "comparison": {
            k: round(bench[k] - GLOBAL_AVG[k], 2) for k in GLOBAL_AVG
        }
    }

@app.get("/analytics/country-ranking")
def country_ranking():
    ranked = sorted(BENCHMARKS.items(), key=lambda x: x[1]["esg_rank"])
    return [{"country": k, **v} for k, v in ranked]

# ============ CLUSTERING ============
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler


@app.get("/analytics/correlation")
def correlation_matrix():
    db = get_db()
    rows = db.execute("SELECT budget, co2_reduction, social_impact, duration_months, total_score FROM evaluations").fetchall()
    if len(rows) < 3:
        return {"error": "Need at least 3 projects"}
    import numpy as np
    cols = ["budget","co2_reduction","social_impact","duration_months","total_score"]
    data = np.array([[r[c] for c in cols] for r in rows], dtype=float)
    corr = np.corrcoef(data.T).tolist()
    return {"labels": cols, "matrix": corr}

# ============ MONTE CARLO ============
import numpy as np

@app.post("/analytics/montecarlo")
def monte_carlo(project: ProjectInput, n_simulations: int = 1000):
    base = [project.budget, project.co2_reduction, project.social_impact, project.duration_months]
    results = []
    for _ in range(n_simulations):
        noise = np.random.normal(1.0, 0.15, 4)
        sim = [max(0, base[i]*noise[i]) for i in range(4)]
        sim[2] = min(10, max(1, sim[2]))
        feats = make_features(ProjectInput(budget=sim[0], co2_reduction=sim[1],
                                            social_impact=sim[2], duration_months=sim[3]))
        prob = float(ensemble_model.predict_proba(feats)[0][1])
        results.append(round(prob*100, 2))
    results.sort()
    return {
        "simulations": n_simulations,
        "mean_probability": round(np.mean(results), 2),
        "median_probability": round(np.median(results), 2),
        "std": round(np.std(results), 2),
        "p5": round(np.percentile(results, 5), 2),
        "p25": round(np.percentile(results, 25), 2),
        "p75": round(np.percentile(results, 75), 2),
        "p95": round(np.percentile(results, 95), 2),
        "histogram": results
    }

# ============ MODEL COMPARISON ============
@app.post("/analytics/model-compare")
def compare_models(data: ProjectInput):
    feats = make_features(data)
    results = {}
    for name, mdl in [("RandomForest", rf_model), ("XGBoost", xgb_model)]:
        prob = float(mdl.predict_proba(feats)[0][1])
        results[name] = {"probability": round(prob*100, 2), "prediction": int(prob >= 0.5)}
    # Stacking
    prob_s = float(ensemble_model.predict_proba(feats)[0][1])
    results["Stacking Ensemble"] = {"probability": round(prob_s*100, 2), "prediction": int(prob_s >= best_threshold)}
    # Neural
    import torch
    nt = torch.FloatTensor(feats if isinstance(feats, np.ndarray) else feats.values)
    with torch.no_grad():
        prob_n = float(nn_model(nt).item())
    results["Neural Network"] = {"probability": round(prob_n*100, 2), "prediction": int(prob_n >= 0.5)}
    return {"models": results, "best_model": max(results.keys(), key=lambda k: results[k]["probability"])}
