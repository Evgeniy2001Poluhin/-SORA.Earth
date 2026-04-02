from app import cache, external_data
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
from app.rate_limit import limiter, rate_limit_handler, SlowAPIMiddleware, RateLimitExceeded
from app.logging_config import setup_logging
from app.middleware import MetricsMiddleware, METRICS
from app.validators import ProjectInput
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import pickle, numpy as np, math, os, json, logging, shap
import pandas as pd
from datetime import datetime
import platform
import torch
import torch.nn as tnn
import warnings
import sqlite3
import csv
import sentry_sdk

warnings.filterwarnings("ignore", message="X does not have valid feature names")

from app.schemas import ProjectInput as Project, GHGInput
from app.api import auth as auth_api
from app.api import evaluate as evaluate_api
from app.api import predict as predict_api
from app.api import analytics as analytics_api
from app.api import system as system_api
from app.api import infra as infra_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sora")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
DB_PATH = os.path.join(ROOT_DIR, "data", "history.db")
PRED_LOG = os.path.join(ROOT_DIR, "data", "predictions_log.csv")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS evaluations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, budget REAL, co2_reduction REAL, social_impact REAL,
        duration_months INTEGER, total_score REAL, environment_score REAL,
        social_score REAL, economic_score REAL, success_probability REAL,
        recommendation TEXT, risk_level TEXT, created_at TEXT,
        region TEXT DEFAULT 'Europe', lat REAL DEFAULT 50.0, lon REAL DEFAULT 10.0)"""
    )
    conn.commit()
    conn.close()


def log_prediction(endpoint, input_data, result):
    file_exists = os.path.exists(PRED_LOG)
    with open(PRED_LOG, "a", newline="") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(["timestamp", "endpoint", "budget", "co2_reduction", "social_impact", "duration_months", "prediction", "probability"])
        w.writerow([
            datetime.now().isoformat(), endpoint,
            input_data.budget, input_data.co2_reduction, input_data.social_impact,
            input_data.duration_months, result.get("prediction", ""), result.get("probability", ""),
        ])


# ===== APP =====
app = FastAPI(
    swagger_ui_parameters={"defaultModelsExpandDepth": -1, "docExpansion": "list", "filter": True},
    redoc_url="/redoc",
    title="SORA.Earth AI Platform",
    version="2.0.0",
)

# Sentry
if os.getenv("SENTRY_DSN"):
    sentry_sdk.init(dsn=os.getenv("SENTRY_DSN"), traces_sample_rate=0.1, environment=os.getenv("SORA_ENV", "development"))

# Logging
logger = setup_logging()

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(MetricsMiddleware)

# Static
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# ===== MODELS =====
with open(os.path.join(ROOT_DIR, "models", "scaler.pkl"), "rb") as f:
    scaler = pickle.load(f)
with open(os.path.join(ROOT_DIR, "models", "model.pkl"), "rb") as f:
    rf_model = pickle.load(f)
with open(os.path.join(ROOT_DIR, "models", "meta.json"), "r") as f:
    model_meta = json.load(f)
with open(os.path.join(ROOT_DIR, "models", "xgb_model.pkl"), "rb") as f:
    xgb_model = pickle.load(f)
with open(os.path.join(ROOT_DIR, "models", "metrics.json"), "r") as f:
    model_metrics = json.load(f)
with open(os.path.join(ROOT_DIR, "models", "stacking_meta.pkl"), "rb") as f:
    stacking_meta = pickle.load(f)
with open(os.path.join(ROOT_DIR, "models", "best_threshold.pkl"), "rb") as f:
    best_threshold = pickle.load(f)["threshold"]

ENS_PATH = os.path.join(ROOT_DIR, "models", "ensemble_model.pkl")
ensemble_model = None
if os.path.exists(ENS_PATH):
    with open(ENS_PATH, "rb") as f:
        ensemble_model = pickle.load(f)


class SoraNet(tnn.Module):
    def __init__(self):
        super().__init__()
        self.net = tnn.Sequential(
            tnn.Linear(7, 64), tnn.ReLU(), tnn.BatchNorm1d(64), tnn.Dropout(0.3),
            tnn.Linear(64, 32), tnn.ReLU(), tnn.BatchNorm1d(32), tnn.Dropout(0.2),
            tnn.Linear(32, 16), tnn.ReLU(), tnn.Linear(16, 1), tnn.Sigmoid(),
        )

    def forward(self, x):
        return self.net(x)


nn_model = SoraNet()
NN_PATH = os.path.join(ROOT_DIR, "models", "pytorch_mlp.pth")
if os.path.exists(NN_PATH):
    nn_model.load_state_dict(torch.load(NN_PATH, map_location="cpu"))
    nn_model.eval()

init_db()
logger.info("SORA.Earth AI Platform started")
explainer_shap = shap.TreeExplainer(rf_model)

# ===== SHARED FUNCTIONS =====
FEATURE_COLS = ["budget", "co2_reduction", "social_impact", "duration_months", "budget_per_month", "co2_per_dollar", "efficiency_score"]


def make_features(data):
    budget_per_month = data.budget / max(data.duration_months, 1)
    co2_per_dollar = data.co2_reduction / max(data.budget, 1) * 1000
    efficiency_score = (data.co2_reduction * data.social_impact) / max(data.duration_months, 1)
    df = pd.DataFrame(
        [[data.budget, data.co2_reduction, data.social_impact, data.duration_months, budget_per_month, co2_per_dollar, efficiency_score]],
        columns=FEATURE_COLS,
    )
    return pd.DataFrame(scaler.transform(df), columns=FEATURE_COLS)


COUNTRIES = {
    "Afghanistan": {"lat": 33.9, "lon": 67.7, "region": "Asia"},
    "Albania": {"lat": 41.2, "lon": 20.2, "region": "Europe"},
    "Algeria": {"lat": 28.0, "lon": 1.7, "region": "Africa"},
    "Argentina": {"lat": -38.4, "lon": -63.6, "region": "South America"},
    "Australia": {"lat": -25.3, "lon": 133.8, "region": "Oceania"},
    "Austria": {"lat": 47.5, "lon": 14.6, "region": "Europe"},
    "Brazil": {"lat": -14.2, "lon": -51.9, "region": "South America"},
    "Canada": {"lat": 56.1, "lon": -106.3, "region": "North America"},
    "China": {"lat": 35.9, "lon": 104.2, "region": "Asia"},
    "France": {"lat": 46.2, "lon": 2.2, "region": "Europe"},
    "Germany": {"lat": 51.2, "lon": 10.5, "region": "Europe"},
    "India": {"lat": 20.6, "lon": 79.0, "region": "Asia"},
    "Italy": {"lat": 41.9, "lon": 12.6, "region": "Europe"},
    "Japan": {"lat": 36.2, "lon": 138.3, "region": "Asia"},
    "Mexico": {"lat": 23.6, "lon": -102.6, "region": "North America"},
    "Nigeria": {"lat": 9.1, "lon": 8.7, "region": "Africa"},
    "Russia": {"lat": 61.5, "lon": 105.3, "region": "Europe"},
    "South Africa": {"lat": -30.6, "lon": 22.9, "region": "Africa"},
    "Spain": {"lat": 40.5, "lon": -3.7, "region": "Europe"},
    "United Kingdom": {"lat": 55.4, "lon": -3.4, "region": "Europe"},
    "United States": {"lat": 37.1, "lon": -95.7, "region": "North America"},
}

REGIONAL_FACTORS = {
    "Europe": {"env_mult": 1.1, "soc_mult": 1.05, "eco_mult": 1.0, "renewable_bonus": 0.05},
    "North America": {"env_mult": 1.0, "soc_mult": 1.0, "eco_mult": 1.1, "renewable_bonus": 0.03},
    "South America": {"env_mult": 1.15, "soc_mult": 0.9, "eco_mult": 0.85, "renewable_bonus": 0.08},
    "Asia": {"env_mult": 0.9, "soc_mult": 0.95, "eco_mult": 1.05, "renewable_bonus": 0.02},
    "Africa": {"env_mult": 1.2, "soc_mult": 0.85, "eco_mult": 0.75, "renewable_bonus": 0.1},
    "Oceania": {"env_mult": 1.05, "soc_mult": 1.1, "eco_mult": 1.0, "renewable_bonus": 0.06},
    "Middle East": {"env_mult": 0.85, "soc_mult": 0.9, "eco_mult": 1.15, "renewable_bonus": 0.01},
}


def calculate_esg(project, region_name: str = "Europe"):
    rf = REGIONAL_FACTORS.get(region_name, REGIONAL_FACTORS["Europe"])
    score_env = min(project.co2_reduction / 100.0 * rf["env_mult"] + rf["renewable_bonus"], 1.0)
    score_soc = min(project.social_impact / 10.0 * rf["soc_mult"], 1.0)
    score_eco = min(1.0 / (1.0 + math.exp(-0.00005 * (project.budget - 50000))) * rf["eco_mult"], 1.0)
    duration_factor = 0.9 if project.duration_months > 48 else (0.95 if project.duration_months > 36 else 1.0)
    total = round((score_env * 0.4 + score_soc * 0.3 + score_eco * 0.3) * duration_factor * 100, 2)
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
        recommendations.append("Budget is moderate. Increasing to $120,000+ would significantly improve economic rating")
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

    risk_level = "Low" if total >= 75 and success_prob >= 70 else ("Medium" if total >= 40 else "High")

    return {
        "total_score": total,
        "environment_score": round(score_env * 100, 1),
        "social_score": round(score_soc * 100, 1),
        "economic_score": round(score_eco * 100, 1),
        "success_probability": success_prob,
        "recommendations": recommendations,
        "risk_level": risk_level,
        "esg_weights": {"environment": 0.4, "social": 0.3, "economic": 0.3},
    }


def _sanitize_pdf(text):
    return str(text).encode("ascii", "ignore").decode("ascii").strip()


# ===== ROUTERS =====
app.include_router(auth_api.router)
app.include_router(evaluate_api.router)
app.include_router(predict_api.router)
app.include_router(analytics_api.router)
app.include_router(system_api.router)
app.include_router(infra_api.router)

from app.api import data_pipeline as data_api
from app.api import retrain as retrain_api
app.include_router(data_api.router)
app.include_router(retrain_api.router)

# Prometheus
Instrumentator().instrument(app).expose(app)


# ===== MINIMAL ENDPOINTS (root, health, model-info) =====
@app.get("/")
def read_root():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


@app.get("/health")
def health():
    return {"status": "ok", "models_loaded": True, "version": "2.0.0"}


@app.get("/system/health", tags=["monitoring"])
def system_health():
    return {
        "status": "ok",
        "app_version": "2.0.0",
        "python_version": platform.python_version(),
        "server_time_utc": datetime.utcnow().isoformat() + "Z",
        "components": {
            "api": "ok",
            "ml_models": "ok" if rf_model and xgb_model and nn_model else "warn",
            "pdf_report": "ok",
            "metrics": "ok",
        },
    }


@app.get("/model-info")
def model_info():
    return model_meta


@app.get("/model-metrics")
def get_model_metrics():
    return model_metrics

# --- Startup/shutdown lifecycle ---
from concurrent.futures import ProcessPoolExecutor
import asyncio

_executor: ProcessPoolExecutor = None

@app.on_event("startup")
async def startup_event():
    global _executor
    _executor = ProcessPoolExecutor(max_workers=4)
    app.state.executor = _executor

@app.on_event("shutdown")
async def shutdown_event():
    app.state.executor.shutdown(wait=True)
