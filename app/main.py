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
# import sqlite3  # replaced by SQLAlchemy
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
from app.api import explain as explain_api
from app.api import calibration as calibration_api
from app.api import scheduler_routes
from app.api import drift_monitor
from app.api import ab_comparison as ab_comparison_api
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sora")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
DB_PATH = os.path.join(ROOT_DIR, "data", "history.db")
PRED_LOG = os.path.join(ROOT_DIR, "data", "predictions_log.csv")


def get_db():
    """SQLAlchemy session generator (use as dependency or context manager)."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_sync():
    """Non-generator version for direct use (not as FastAPI Depends)."""
    from app.database import SessionLocal
    return SessionLocal()


def init_db():
    from app.database import init_db as _init
    _init()


def log_prediction(endpoint, input_data, result, latency_ms=None):
    try:
        from app.database import PredictionLog
        db = get_db_sync()
        log = PredictionLog(
            endpoint=endpoint,
            budget=input_data.budget,
            co2_reduction=input_data.co2_reduction,
            social_impact=input_data.social_impact,
            duration_months=input_data.duration_months,
            category=getattr(input_data, "category", None),
            region=getattr(input_data, "region", None),
            prediction=result.get("prediction"),
            probability=result.get("probability") or result.get("success_probability"),
            esg_total_score=result.get("total_score"),
            latency_ms=latency_ms,
        )
        db.add(log)
        db.commit()
        db.close()
    except Exception as e:
        logger.error(f"log_prediction failed: {e}")

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

# /v1/ prefix support: rewrite path before routing
@app.middleware("http")
async def v1_prefix_rewrite(request, call_next):
    if request.url.path.startswith("/v1/") or request.url.path == "/v1":
        request.scope["path"] = request.scope["path"][3:] or "/"
        if "raw_path" in request.scope:
            rp = request.scope["raw_path"]
            if rp.startswith(b"/v1"):
                request.scope["raw_path"] = rp[3:] or b"/"
    return await call_next(request)

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

# v2 stacking model
try:
    with open(os.path.join(ROOT_DIR, "models", "cat_encodings.json")) as _f:
        cat_encodings = json.load(_f)
    with open(os.path.join(ROOT_DIR, "models", "scaler_v2.pkl"), "rb") as _f:
        scaler_v2 = pickle.load(_f)
    _cal = os.path.join(ROOT_DIR, "models", "ensemble_model_v2_cal.pkl")
    _ens = os.path.join(ROOT_DIR, "models", "ensemble_model_v2.pkl")
    with open(_cal if os.path.exists(_cal) else _ens, "rb") as _f:
        ensemble_model_v2 = pickle.load(_f)
    FEATURE_COLS_V2 = ["budget","co2_reduction","social_impact","duration_months",
                       "budget_per_month","co2_per_dollar","efficiency_score",
                       "impact_ratio","budget_efficiency","category_enc","region_enc"]
    logger.info("ensemble_model_v2 loaded OK (CV AUC=0.82)")
except Exception as _e:
    ensemble_model_v2 = None; scaler_v2 = None; FEATURE_COLS_V2 = []
    logger.warning(f"ensemble_model_v2 not loaded: {_e}")
if os.path.exists(ENS_PATH):
    with open(ENS_PATH, "rb") as f:
        ensemble_model = pickle.load(f)


class SoraNet(tnn.Module):
    def __init__(self):
        super().__init__()
        self.net = tnn.Sequential(
            tnn.Linear(9, 64), tnn.ReLU(), tnn.BatchNorm1d(64), tnn.Dropout(0.3),
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

from apscheduler.schedulers.background import BackgroundScheduler as _BGS
_scheduler = _BGS(timezone="UTC")

def _scheduled_retrain():
    try:
        import pandas as pd, pickle, shap as _shap
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.preprocessing import StandardScaler
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import roc_auc_score, f1_score, accuracy_score
        from datetime import datetime as _dt
        _df = pd.read_csv(os.path.join(ROOT_DIR, "data", "projects.csv"))
        _df["budget_per_month"] = _df["budget"] / _df["duration_months"].clip(lower=1)
        _df["co2_per_dollar"]   = _df["co2_reduction"] / _df["budget"].clip(lower=1) * 1000
        _df["efficiency_score"] = (_df["co2_reduction"] * _df["social_impact"]) / _df["duration_months"].clip(lower=1)
        _n = _dt.utcnow()
        _df["year"] = _n.year; _df["quarter"] = (_n.month - 1) // 3 + 1
        _cols = ["budget","co2_reduction","social_impact","duration_months","budget_per_month","co2_per_dollar","efficiency_score","year","quarter"]
        _X = _df[_cols]; _y = _df["success"].values
        _Xtr, _Xte, _ytr, _yte = train_test_split(_X, _y, test_size=0.2, random_state=42, stratify=_y)
        _sc = StandardScaler(); _Xtr_s = pd.DataFrame(_sc.fit_transform(_Xtr), columns=_cols); _Xte_s = pd.DataFrame(_sc.transform(_Xte), columns=_cols)
        _rf = RandomForestClassifier(n_estimators=200, max_depth=10, min_samples_leaf=2, random_state=42, n_jobs=-1)
        _rf.fit(_Xtr_s, _ytr)
        _auc = round(roc_auc_score(_yte, _rf.predict_proba(_Xte_s)[:,1]), 4)
        _f1  = round(f1_score(_yte, _rf.predict(_Xte_s)), 4)
        _mdir = os.path.join(ROOT_DIR, "models")
        # Backup + AUC guard
        import shutil as _sh
        _rf_path = os.path.join(_mdir, "random_forest.pkl")
        _sc_path = os.path.join(_mdir, "scaler.pkl")
        if os.path.exists(_rf_path):
            _sh.copy2(_rf_path, _rf_path + ".bak")
            _sh.copy2(_sc_path, _sc_path + ".bak")
        if _auc < 0.75:
            logger.warning(f"Retrain REJECTED: AUC={_auc} < 0.75, keeping previous model")
            return
        with open(_rf_path, "wb") as _fh: pickle.dump(_rf, _fh)
        with open(os.path.join(_mdir, "scaler.pkl"), "wb") as _fh: pickle.dump(_sc, _fh)
        global rf_model, scaler, explainer_shap
        rf_model = _rf; scaler = _sc; explainer_shap = _shap.TreeExplainer(_rf)
        global FEATURE_COLS
        FEATURE_COLS = _cols
        from app.mlflow_tracking import log_model_registry
        log_model_registry(_rf, "RandomForest_auto", {"auc": _auc, "f1": _f1})
        logger.info(f"Scheduled retrain OK: AUC={_auc}")
    except Exception as _e:
        logger.error(f"Scheduled retrain FAILED: {_e}")

_scheduler.add_job(_scheduled_retrain, "interval", hours=24, id="auto_retrain", replace_existing=True)
_scheduler.start()
logger.info("APScheduler started: auto_retrain every 24h")

explainer_shap = shap.TreeExplainer(rf_model)

# ===== SHARED FUNCTIONS =====
FEATURE_COLS = ["budget", "co2_reduction", "social_impact", "duration_months", "budget_per_month", "co2_per_dollar", "efficiency_score", "year", "quarter"]

FEATURE_COLS_BASE = ["budget", "co2_reduction", "social_impact", "duration_months",
                     "budget_per_month", "co2_per_dollar", "efficiency_score"]

def log_evaluation(project_name, esg_scores, risk_level):
    try:
        from app.mlflow_tracking import log_evaluation as _log_eval
        _log_eval(project_name, esg_scores, risk_level)
    except Exception:
        pass


def make_features(data):
    budget_per_month = data.budget / max(data.duration_months, 1)
    co2_per_dollar = data.co2_reduction / max(data.budget, 1) * 1000
    efficiency_score = (data.co2_reduction * data.social_impact) / max(data.duration_months, 1)
    df = pd.DataFrame(
        [[data.budget, data.co2_reduction, data.social_impact, data.duration_months,
          budget_per_month, co2_per_dollar, efficiency_score,
          __import__("datetime").datetime.utcnow().year,
          (__import__("datetime").datetime.utcnow().month - 1) // 3 + 1]],
        columns=FEATURE_COLS,
    )
    return pd.DataFrame(scaler.transform(df), columns=FEATURE_COLS)


def make_features_base(data):
    """Returns 7-feature scaled DataFrame for RF/XGB/Ensemble (original training schema)."""
    feats_full = make_features(data)
    return feats_full[FEATURE_COLS_BASE]
def make_features_v2(data, category: str = "Solar Energy", region: str = "Europe"):
    if scaler_v2 is None:
        return make_features(data)
    bpm = data.budget / max(data.duration_months, 1)
    c2d = data.co2_reduction / max(data.budget, 1) * 1000
    eff = (data.co2_reduction * data.social_impact) / max(data.duration_months, 1)
    ir  = data.social_impact / max(data.co2_reduction, 1)
    be  = data.co2_reduction / max(bpm, 1)
    c_enc = cat_encodings.get("category", {}).get(category, 0.5)
    r_enc = cat_encodings.get("region", {}).get(region, 0.5)
    row = [[data.budget, data.co2_reduction, data.social_impact, data.duration_months,
            bpm, c2d, eff, ir, be, c_enc, r_enc]]
    df = pd.DataFrame(row, columns=FEATURE_COLS_V2)
    return pd.DataFrame(scaler_v2.transform(df), columns=FEATURE_COLS_V2)


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

    category = getattr(project, "category", "Solar Energy")
    region   = getattr(project, "region",   "Europe")
    if ensemble_model_v2 is not None:
        feats_v2 = make_features_v2(project, category, region)
        success_prob_v2 = round(float(ensemble_model_v2.predict_proba(feats_v2)[0][1]) * 100, 2)
    else:
        success_prob_v2 = success_prob

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

    result = {
        "total_score": total,
        "environment_score": round(score_env * 100, 1),
        "social_score": round(score_soc * 100, 1),
        "economic_score": round(score_eco * 100, 1),
        "success_probability": success_prob,
        "success_probability_v2": success_prob_v2,
        "recommendations": recommendations,
        "risk_level": risk_level,
        "esg_weights": {"environment": 0.4, "social": 0.3, "economic": 0.3},
    }
    project_name = getattr(project, "name", "unknown")
    log_evaluation(project_name, result, risk_level)
    return result


def _sanitize_pdf(text):
    return str(text).encode("ascii", "ignore").decode("ascii").strip()


# ===== ROUTERS =====
from fastapi import APIRouter as _APIRouter

from app.api import data_pipeline as data_api
from app.api import retrain as retrain_api
from app.api import drift as drift_api
from app.api import compare as compare_api
from app.api import ab_test as ab_api

_all_routers = [
    auth_api.router, evaluate_api.router, predict_api.router,
    analytics_api.router, system_api.router, infra_api.router,
    data_api.router, retrain_api.router, drift_api.router,
    compare_api.router, ab_api.router, explain_api.router,
    calibration_api.router, ab_comparison_api.router, scheduler_routes.router, drift_monitor.router,
]

# Include all routers with /api/v1 prefix + backward-compatible original paths
from fastapi import APIRouter
api_v1 = APIRouter(prefix="/api/v1")
for _r in _all_routers:
    api_v1.include_router(_r)
from app.auth_routes import router as auth_router
api_v1.include_router(auth_router)
app.include_router(api_v1)

# Backward compatibility: original paths (no prefix)
for _r in _all_routers:
    app.include_router(_r)
app.include_router(auth_router)


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
    from app.scheduler import init_scheduler
    init_scheduler()

@app.on_event("shutdown")
async def shutdown_event():
    from app.scheduler import shutdown_scheduler
    shutdown_scheduler()
    app.state.executor.shutdown(wait=True)
