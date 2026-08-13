# First, before anything else imports a secret or opens a socket. A placeholder
# that gets as far as serving traffic is signing tokens anyone holding the same
# public example can forge, so production refuses to start rather than start
# wrongly. Development is untouched.
from app.secret_validation import enforce as _enforce_secrets

_enforce_secrets()

from app import cache, external_data
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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
from app.routes import map_russia
# import sqlite3  # replaced by SQLAlchemy
import csv
import sentry_sdk

warnings.filterwarnings("ignore", message="X does not have valid feature names")
warnings.filterwarnings("ignore", message="X has feature names, but")

from app.schemas import ProjectInput as Project, GHGInput
from app.api import auth as auth_api
from app.api import evaluate as evaluate_api
from app.api import predict as predict_api
from app.api import admin_retrain_log
from app.api import analytics as analytics_api
from app.api import system as system_api
from app.api import infra as infra_api
from app.api import explain as explain_api
from app.api import calibration as calibration_api
from app.api import scheduler_routes
from app.api import webhooks as webhooks_api
from app.api import status_page as status_page_api
from app.api import drift_monitor
from app.api import ab_comparison as ab_comparison_api
from app.api import compliance as compliance_api
from app.api import rag_api
from app.api import sentinel_api
from app.api import map_data as map_api
from app.api import predict_v2 as predict_v2_api
from app.ml.routes import router as ml_v2_router
from app.api import reports as reports_api
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


def _type_check_applies(engine):
    """Types are compared on PostgreSQL, and only there.

    SQLite is the suite's database, and it stores what it is given: `VARCHAR(50)`
    round-trips, but its type affinity means a length is never enforced, so a
    mismatch there says nothing about production. PostgreSQL is where a length,
    a precision or an enum actually rejects a value, so that is where the deeper
    contract is checked -- and the reduced one is what SQLite gets.
    """
    return engine.dialect.name == "postgresql"


def _compile_type(type_, engine):
    """For the message. Never for the decision -- see _type_is_compatible."""
    if type_ is None:
        return None
    try:
        return type_.compile(dialect=engine.dialect)
    except Exception:
        return None


def _type_family(type_):
    import sqlalchemy as sa

    for base in (sa.String, sa.Boolean, sa.Integer, sa.Float, sa.Numeric,
                 sa.DateTime, sa.Date, sa.Time, sa.LargeBinary, sa.JSON):
        if isinstance(type_, base):
            return base.__name__
    return type_.__class__.__name__


def _type_is_compatible(model_type, db_type):
    """Compatibility, not equality. Equality refuses correct databases.

    Measured against a canonical PostgreSQL schema built by the migrations
    themselves: comparing rendered types flagged **42 columns** -- 40 of them
    artefacts of the comparison, 2 genuine drift. The 40 look like --

        FLOAT          vs DOUBLE PRECISION   the same PostgreSQL type
        VARCHAR(50)    vs VARCHAR            a column the DDL left unbounded

    Strict equality would therefore have refused to start on every correct
    production database. It also refuses a widening -- VARCHAR(50) to
    VARCHAR(100) -- which is exactly the compatible change a rolling deploy
    makes while the old version is still running.

    So: same family, and the database no narrower than the models. Under that
    relation 40 of the 42 vanish and 2 remain, both genuine.
    """
    if _type_family(model_type) != _type_family(db_type):
        return False

    model_length = getattr(model_type, "length", None)
    db_length = getattr(db_type, "length", None)
    # db_length None means unbounded, which is never narrower.
    if model_length is not None and db_length is not None and db_length < model_length:
        return False

    for attribute in ("precision", "scale"):
        declared = getattr(model_type, attribute, None)
        actual = getattr(db_type, attribute, None)
        if declared is not None and actual is not None and actual < declared:
            return False

    # Timezone is not a width, and a naive column cannot hold what an aware one
    # does. The two surviving disagreements above are exactly this.
    model_tz = getattr(model_type, "timezone", None)
    db_tz = getattr(db_type, "timezone", None)
    if model_tz is not None and db_tz is not None and model_tz != db_tz:
        return False

    return True


def assert_schema_ready(engine=None):
    """Alembic owns the schema. The application checks it and does not build it.

    This used to be `init_db()`, which called `Base.metadata.create_all()` at
    import time. That made importing the application a schema-changing act -- for
    a web worker, the scheduler, a CLI, or a test collection run alike -- and it
    quietly papered over a database that migrations had left incomplete, which is
    exactly how four tables came to have no migration at all (issue #51).

    So the application now reads and refuses. A missing table is a provisioning
    fault, and the deploy that skipped `alembic upgrade head` is the thing to fix;
    creating it here would hide that from whoever needs to know.

    It checks **columns as well as tables**. Names alone are not the contract: a
    schema left behind by an older revision has every table this application
    expects, passes a name-only check, starts, and then fails on the first query
    naming a column added since -- at request time, to a user, instead of at
    startup, to the deploy.

    Columns rather than the recorded Alembic revision, because a revision number
    says what was *run*, not what is *there*. A hand-edited table or a migration
    that failed halfway leaves the version row untouched and the schema wrong,
    which is exactly the case worth catching. The revision is also not available
    without reading the migration graph at runtime.

    What it checks, and what it does not -- stated rather than left to be
    discovered. The depth depends on the dialect, because SQLite is the suite's
    database and must not set the contract production is held to:

        everywhere       every model table exists
                         every model column exists
                         nullability agrees, in both directions

        PostgreSQL too   the column type is compatible: same family, and the
                         database no narrower in length, precision or scale.
                         Enum *labels* are not compared -- sa.Enum lands in the
                         String family, so a database enum missing a label
                         passes

        still unchecked  indexes, constraints, triggers, rules, row-level
                         security, and anything extra

    Nullability is checked in both directions and a server default on the
    *database* side does not excuse it. A default applies only to a column the
    statement omits, and whether the statement omits it depends on the mapping,
    not on the database -- measured:

        mapped column without server_default   INSERT names it, sends NULL,
                                               so the default never applies
        mapped column with server_default      INSERT omits it, the default
                                               does apply

    None of the models here declare `server_default`, so on this codebase the
    column is always named and a database-side default is never reached. That is
    a fact about these mappings, not about SQLAlchemy in general.

    The reverse direction -- nullable in the database, NOT NULL in the models --
    lets reads return None where the models say they cannot.

    Not a claim that these are the only things that break a write. A type, a
    length, an enum, a constraint or a trigger will too; the unchecked list above
    is a real gap, and the migration-time check in f2c9a1d47b30 is what covers
    indexes, constraints, triggers, rules and row-level security on PostgreSQL.

    Deliberately no environment variable to switch this off. An escape hatch would
    be used, and the first time it is used is the moment the guarantee stops
    meaning anything.

    The `engine` argument exists so a test can point the check at a database it
    controls; startup passes nothing and gets the application's own.
    """
    from sqlalchemy import inspect

    from app.database import Base
    from app.database import engine as app_engine

    engine = engine or app_engine
    inspector = inspect(engine)
    present = set(inspector.get_table_names())

    faults = []
    for name, table in sorted(Base.metadata.tables.items()):
        if name not in present:
            faults.append("missing table: %s" % name)
            continue
        # Names alone are not the contract. A schema left behind by an older
        # revision has every table the application expects and is still wrong;
        # checking only names lets it start and fail later on the first query
        # that names a column added since.
        reflected = {c["name"]: c for c in inspector.get_columns(name)}
        for column in sorted(set(table.columns.keys()) - set(reflected)):
            faults.append("missing column: %s.%s" % (name, column))

        for column_name, model_column in sorted(table.columns.items()):
            found = reflected.get(column_name)
            if found is None:
                continue

            # Nullability, in both directions, and without excusing a server
            # default. A default only applies to a column the statement omits:
            # measured on PostgreSQL, `INSERT ... (note) VALUES (NULL)` against
            # `note text NOT NULL DEFAULT 'x'` still raises a not-null violation,
            # and code holding an optional column may pass None explicitly.
            db_nullable = bool(found.get("nullable", True))
            if db_nullable != bool(model_column.nullable):
                faults.append(
                    "%s.%s is %s in the database and %s in the models -- %s"
                    % (name, column_name,
                       "nullable" if db_nullable else "NOT NULL",
                       "nullable" if model_column.nullable else "NOT NULL",
                       "writes may fail" if not db_nullable else
                       "reads may return None where the models say they cannot"))

            # Types only where the comparison means something. Reflected types
            # are dialect-specific, so this compiles both sides with the engine's
            # own dialect rather than comparing across dialects -- which is what
            # would refuse a schema that is in fact correct.
            if _type_check_applies(engine) and found.get("type") is not None:
                if not _type_is_compatible(model_column.type, found["type"]):
                    faults.append(
                        "%s.%s is %s in the database and %s in the models"
                        % (name, column_name,
                           _compile_type(found["type"], engine),
                           _compile_type(model_column.type, engine)))

    if faults:
        raise RuntimeError(
            "the database does not match what the application expects (%d "
            "problem(s)):\n%s\n"
            "Run `alembic upgrade head` against this database. The application "
            "does not create or alter tables; migrations do."
            % (len(faults), "\n".join(faults))
        )


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


def _custom_unique_id(route):
    """Deterministic operationId: tag_name. Avoids duplicates across routers."""
    tag = (route.tags[0] if route.tags else "default")
    tag = tag.replace(" ", "_").replace("-", "_").replace("/", "_").lower()
    return tag + "_" + route.name

app = FastAPI(
    generate_unique_id_function=_custom_unique_id,
    swagger_ui_parameters={"defaultModelsExpandDepth": -1, "docExpansion": "list", "filter": True},
    redoc_url="/redoc",
    title="SORA.Earth AI Platform",
    version="2.0.0",
)

@app.middleware("http")
async def head_to_get(request, call_next):
    """Convert HEAD to GET internally, then return empty body (RFC 7231).

    Do NOT set Content-Length manually — it conflicts with the streamed
    body_iterator and causes 'Ru longer than
    Content-Length' in uvicorn. Let Starlette/Uvicorn compute it.
    """
    if request.method == "HEAD":
        request.scope["method"] = "GET"
        response = await call_next(request)
        from starlette.responses import Response as _Resp
        empty = _Resp(
            content=b"",
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )
        if "content-length" in empty.headers:
            del empty.headers["content-length"]
        if "transfer-encoding" in empty.headers:
            del empty.headers["transfer-encoding"]
        return empty
    return await call_next(request)


origins = [
    "https://sora-earth.ru",
    "https://www.sora-earth.ru",
    "http://localhost:8081",
    "http://127.0.0.1:8081",
    "http://localhost:3000",
    "http://localhost:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
                       "impact_ratio","budget_efficiency","category_enc","region_enc",
                       "country_gdp_per_capita"]
    logger.info("ensemble_model_v2 loaded OK (12 features incl country_gdp_per_capita)")
except Exception as _e:
    ensemble_model_v2 = None; scaler_v2 = None; FEATURE_COLS_V2 = []
    logger.warning(f"ensemble_model_v2 not loaded: {_e}")

# country -> GDP-per-capita lookup for the v2 model's country_gdp_per_capita feature
try:
    with open(os.path.join(ROOT_DIR, "models", "country_gdp.json")) as _f:
        COUNTRY_GDP = json.load(_f)
except Exception:
    COUNTRY_GDP = {"by_iso": {}, "by_name": {}, "median": 0.0}


def gdp_for_country(name):
    """Resolve a request's country/region string to a GDP-per-capita value.
    Tries the country short-name, then ISO code, then name->ISO via
    external_data.COUNTRY_ISO3; falls back to the dataset median."""
    median = COUNTRY_GDP.get("median", 0.0)
    if not name:
        return median
    by_name = COUNTRY_GDP.get("by_name", {})
    by_iso = COUNTRY_GDP.get("by_iso", {})
    if name in by_name:
        return by_name[name]
    if name in by_iso:
        return by_iso[name]
    try:
        from app.external_data import COUNTRY_ISO3
        iso = COUNTRY_ISO3.get(name)
        if iso and iso in by_iso:
            return by_iso[iso]
    except Exception:
        pass
    return median
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

logger.info("SORA.Earth AI Platform started")

explainer_shap = shap.TreeExplainer(rf_model)

# ===== SHARED FUNCTIONS =====
FEATURE_COLS = ["budget", "co2_reduction", "social_impact", "duration_months", "budget_per_month", "co2_per_dollar", "efficiency_score", "year", "quarter"]

FEATURE_COLS_BASE = ["budget", "co2_reduction", "social_impact", "duration_months", "budget_per_month", "co2_per_dollar", "impact_per_month"]

def log_evaluation(project_name, esg_scores, risk_level):
    try:
        from app.mlflow_tracking import log_evaluation as _log_eval
        _log_eval(project_name, esg_scores, risk_level)
    except Exception:
        pass


def make_features(data):
    """Returns 9-feature DataFrame consistent with retrain feature_cols."""
    from datetime import datetime as _dt

    budget_per_month = data.budget / max(data.duration_months, 1)
    co2_per_dollar = data.co2_reduction / max(data.budget, 1) * 1000
    efficiency_score = (data.co2_reduction * data.social_impact) / max(data.duration_months, 1)
    year = _dt.utcnow().year
    quarter = (_dt.utcnow().month - 1) // 3 + 1

    df9 = pd.DataFrame(
        [[
            data.budget,
            data.co2_reduction,
            data.social_impact,
            data.duration_months,
            budget_per_month,
            co2_per_dollar,
            efficiency_score,
            year,
            quarter,
        ]],
        columns=[
            "budget",
            "co2_reduction",
            "social_impact",
            "duration_months",
            "budget_per_month",
            "co2_per_dollar",
            "efficiency_score",
            "year",
            "quarter",
        ],
    )

    scaled = pd.DataFrame(scaler.transform(df9), columns=df9.columns)
    return scaled[FEATURE_COLS]

def make_features_xgb(data):
    """7-feature unscaled DataFrame for legacy XGBoost."""
    budget_per_month = data.budget / max(data.duration_months, 1)
    co2_per_dollar = data.co2_reduction / max(data.budget, 1) * 1000
    impact_per_month = data.social_impact / max(data.duration_months, 1)
    return pd.DataFrame(
        [[data.budget, data.co2_reduction, data.social_impact,
          data.duration_months, budget_per_month, co2_per_dollar,
          impact_per_month]],
        columns=FEATURE_COLS_BASE,
    )

def make_features_base(data):
    """Alias for make_features (9 features)."""
    return make_features(data)

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
    gdp = gdp_for_country(region)  # `region` carries the request's country (aliased)
    row = [[data.budget, data.co2_reduction, data.social_impact, data.duration_months,
            bpm, c2d, eff, ir, be, c_enc, r_enc, gdp]]
    df = pd.DataFrame(row, columns=FEATURE_COLS_V2)
    return pd.DataFrame(scaler_v2.transform(df), columns=FEATURE_COLS_V2)


COUNTRIES = {
    "Afghanistan": {"lat": 33.9, "lon": 67.7, "region": "Asia"},
    "Albania": {"lat": 41.2, "lon": 20.2, "region": "Europe"},
    "Algeria": {"lat": 28.0, "lon": 1.7, "region": "Africa"},
    "Argentina": {"lat": -38.4, "lon": -63.6, "region": "South America"},
    "Australia": {"lat": -25.3, "lon": 133.8, "region": "Oceania"},
    "Austria": {"lat": 47.5, "lon": 14.6, "region": "Europe"},
    "Sweden": {"lat": 60.1, "lon": 18.6, "region": "Europe"},
    "Norway": {"lat": 60.5, "lon": 8.5, "region": "Europe"},
    "Denmark": {"lat": 56.3, "lon": 9.5, "region": "Europe"},
    "Finland": {"lat": 61.9, "lon": 25.7, "region": "Europe"},
    "Netherlands": {"lat": 52.1, "lon": 5.3, "region": "Europe"},
    "Switzerland": {"lat": 46.8, "lon": 8.2, "region": "Europe"},
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
    "Russia": {"lat": 55.75, "lon": 37.62, "region": "Europe"},
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
    try:
        from app.country_benchmarks import BENCHMARKS, GLOBAL_AVG
        country_key = getattr(project, "region", None) or "Global Average"
        cb = BENCHMARKS.get(country_key, GLOBAL_AVG) if BENCHMARKS else GLOBAL_AVG
    except Exception:
        cb = {"co2_per_capita": 4.7, "renewable_share": 28.3, "hdi": 0.739, "gdp_per_capita": 20000}
    c_co2 = float(cb.get("co2_per_capita", 4.7))
    c_ren = float(cb.get("renewable_share", 28.3))
    c_hdi = float(cb.get("hdi", 0.739))
    c_gdp = float(cb.get("gdp_per_capita", 20000))
    co2_norm = min(project.co2_reduction / 500.0, 1.0)
    country_env = max(0.0, min(1.0, (c_ren / 100.0) * 0.6 + max(0, 15 - c_co2) / 15.0 * 0.4))
    score_env = min((0.55 * co2_norm + 0.45 * country_env) * rf["env_mult"], 1.0)
    country_soc = max(0.4, min(1.0, c_hdi))
    score_soc = min(project.social_impact / 10.0 * country_soc * rf["soc_mult"], 1.0)
    gdp_pivot = max(20000.0, min(c_gdp, 80000.0))
    budget_ratio = project.budget / gdp_pivot
    sig = 1.0 / (1.0 + math.exp(-2.5 * (budget_ratio - 2.0)))
    gdp_bonus = 0.10 * math.tanh((c_gdp - 20000) / 30000)
    score_eco = max(0.0, min(0.85, sig * 0.9 * rf["eco_mult"] + gdp_bonus))
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
        target_co2 = max(int(project.co2_reduction * 1.5), int(project.co2_reduction + (0.7 - score_env) * 200))
        recommendations.append(f"Increase CO2 reduction from {project.co2_reduction:.0f} to {target_co2:.0f}+ t/yr to reach Strong environmental rating")
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
        recommendations.append("[OK] Excellent ESG profile — ready for green bond certification")
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
from app.api import admin_snapshot
from app.api import admin_timeline
from app.api import admin_diagnostics
from app.api import admin_ai_control
from app.api import ai_teammate_routes
from app.api import copilot_api
from app.api.embed import api as embed_api
from app.api.drift_baseline import router as drift_baseline_router
from app.api.explainability import router as explainability_router

_all_routers = [
    evaluate_api.router, predict_api.router,
    analytics_api.router, system_api.router, infra_api.router,
    data_api.router, retrain_api.router, drift_api.router,
    compare_api.router, ab_api.router, explain_api.router,
    calibration_api.router, ab_comparison_api.router, scheduler_routes.router, drift_monitor.router,
    compliance_api.router,
    map_api.router,
    reports_api.router,
]

# Include all routers with /api/v1 prefix + backward-compatible original paths
from fastapi import APIRouter
api_v1 = APIRouter(prefix="/api/v1")
for _r in _all_routers:
    api_v1.include_router(_r)
from app.auth_routes import router as auth_router
api_v1.include_router(embed_api.router)
api_v1.include_router(auth_router)
api_v1.include_router(admin_retrain_log.router)
api_v1.include_router(admin_snapshot.router)
api_v1.include_router(admin_timeline.router)
api_v1.include_router(admin_diagnostics.router)
api_v1.include_router(admin_ai_control.router)
api_v1.include_router(drift_baseline_router)
api_v1.include_router(explainability_router)
api_v1.include_router(copilot_api.router)
app.include_router(api_v1)
app.include_router(webhooks_api.router)
app.include_router(status_page_api.router)

# Backward compatibility: original paths (no prefix)
# DISABLED to avoid duplicate route registration / duplicate OpenAPI operation IDs
# for _r in _all_routers:
#     app.include_router(_r)
# app.include_router(auth_router)
app.include_router(ai_teammate_routes.router, prefix="/api/v1")
app.include_router(ml_v2_router)
app.include_router(map_russia.router)
app.include_router(predict_v2_api.router)
app.include_router(rag_api.router)
from app.api import forecast as forecast_api
app.include_router(forecast_api.router, prefix="/api/v1")
app.include_router(sentinel_api.router)


# --- MLOps domain metrics (Prometheus) ---
from prometheus_client import Counter, Histogram, Gauge

from app import prom_metrics  # noqa
Instrumentator().instrument(app).expose(app)


# ===== MINIMAL ENDPOINTS (root, health, model-info) =====
@app.get("/")
def read_root():
    # app/static/index.html is a symlink to spa/index.html, which is build
    # output. Whenever the SPA has not been built the link dangles, and
    # FileResponse raises RuntimeError at request time rather than returning a
    # status -- a 500 where 404 is the honest answer. os.path.exists() resolves
    # the link, so a missing file and a dangling symlink are covered alike.
    index = os.path.join(BASE_DIR, "static", "index.html")
    if not os.path.exists(index):
        raise HTTPException(status_code=404, detail="UI is not built")
    return FileResponse(index)
@app.get("/dev")
async def dev_page():
    basedir = os.path.dirname(os.path.abspath(__file__))
    return FileResponse(os.path.join(basedir, "static", "dev.html"))



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
    import asyncio

    # First, and synchronously: if the schema is not there, nothing below is
    # worth starting. Here rather than at import time, so that importing the
    # application -- for alembic, a CLI, or test collection -- does not require a
    # reachable database.
    assert_schema_ready()

    # A misconfigured frame policy stops the boot rather than the page.
    #
    # Discovering it on the first embed request would leave the deployment
    # looking healthy -- every other route working, health green -- while the
    # one control that module exists to provide was unusable. Unset is fine and
    # means the documented open default; set-and-malformed is refused, because
    # nobody sets it except to restrict.
    from app.api.embed.api import validate_frame_ancestors_config
    validate_frame_ancestors_config()

    async def _bg_refresh():
        try:
            from app.external_data import refresh_all_countries
            await asyncio.to_thread(refresh_all_countries)
        except Exception:
            pass
    asyncio.create_task(_bg_refresh())

    # Pre-train forecast models in background
    # Note: Redis lock in scheduled_pretrain_forecast_models prevents duplicate work
    # Multiple workers will try, but only first succeeds - others skip silently
    async def _pretrain_forecast():
        """Pre-train all forecast models to eliminate cold-start delay."""
        try:
            from app.scheduler import scheduled_pretrain_forecast_models
            result = await asyncio.to_thread(scheduled_pretrain_forecast_models)
            if result.get("status") != "skipped":
                logger.info("Forecast pre-training complete")
        except Exception as e:
            logger.warning(f"Forecast pre-training failed: {e}", exc_info=True)
    asyncio.create_task(_pretrain_forecast())

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

from fastapi.responses import FileResponse
import os

@app.get("/admin", tags=["admin"])
def admin_dashboard():
    basedir = os.path.dirname(os.path.abspath(__file__))
    return FileResponse(os.path.join(basedir, "static", "admin-dashboard.html"))

# --- UI ROUTES ---
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path as _Path

_STATIC = _Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC), name="static")

# No route for "/" here. read_root() above already registers it, and FastAPI
# matches in registration order, so a second "/" is unreachable -- this one served
# pages/landing.html and never ran. The React SPA is what "/" is meant to answer
# with now; the legacy pages below are still reachable at their own paths.
#
# Whether the legacy UI should survive at all is a separate question and is not
# decided here.
@app.get("/auth/login", response_class=HTMLResponse, include_in_schema=False)
async def _login_page():
    return FileResponse(_STATIC / "pages/login.html")

@app.get("/app/{path:path}", response_class=HTMLResponse, include_in_schema=False)
async def _app_shell(path: str = ""):
    return FileResponse(_STATIC / "pages/app.html")

@app.get("/admin/{path:path}", response_class=HTMLResponse, include_in_schema=False)
async def _admin_shell(path: str = ""):
    return FileResponse(_STATIC / "pages/admin.html")
# --- /UI ROUTES ---

from fastapi.responses import FileResponse as _FR
@app.get("/favicon.ico", include_in_schema=False)
def _favicon(): return _FR("app/static/favicon.ico")

# ===== SORA_SPA_MOUNT =====
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi import HTTPException
from pathlib import Path as _P

_SPA_DIR = _P(__file__).parent / "static"
_SPA_INDEX = _SPA_DIR / "index.html"
_SPA_ASSETS = _SPA_DIR / "assets"

if _SPA_ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=_SPA_ASSETS), name="spa_assets")

if _SPA_INDEX.exists():
    @app.get("/{spa_path:path}", include_in_schema=False)
    async def _sora_spa(spa_path: str):
        blocked = ("api/", "admin/", "health", "metrics",
                   "docs", "openapi.json", "redoc")
        # Security: block sensitive paths
        if spa_path.startswith(blocked) or spa_path.startswith(".") or "/.env" in spa_path or spa_path == ".env":
            raise HTTPException(status_code=404)
        candidate = _SPA_DIR / spa_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_SPA_INDEX)
# ===== /SORA_SPA_MOUNT =====

# === SPA v2 mount ===
from fastapi.staticfiles import StaticFiles as _SPA_Static
from fastapi.responses import FileResponse as _SPA_File
from pathlib import Path as _SPA_Path
_SPA2 = _SPA_Path(__file__).parent / "static" / "spa"
if _SPA2.exists() and (_SPA2 / "assets").exists():
    app.mount("/v2/assets", _SPA_Static(directory=str(_SPA2 / "assets")), name="spa_assets")
    @app.get("/v2", include_in_schema=False)
    def _spa_v2_root():
        return _SPA_File(str(_SPA2 / "index.html"))
    @app.get("/v2/{full_path:path}", include_in_schema=False)
    def _spa_v2_any(full_path: str):
        return _SPA_File(str(_SPA2 / "index.html"))

# === SPA catch-all ===
from fastapi.responses import FileResponse as _CA_File
from pathlib import Path as _CA_Path
from fastapi import Request as _CA_Request
_CA_INDEX = _CA_Path(__file__).parent / "static" / "spa" / "index.html"
_CA_RESERVED = ("api", "static", "health", "metrics", "docs", "redoc", "openapi.json", "ws", "v2", "favicon.svg", "favicon.ico")

@app.get("/{full_path:path}", include_in_schema=False)
def _spa_catchall(full_path: str):
    from fastapi import HTTPException
    top = full_path.split("/", 1)[0]
    # Security: block reserved paths and sensitive files
    if top in _CA_RESERVED:
        raise HTTPException(status_code=404)
    if full_path.startswith(".") or "/.env" in full_path or full_path == ".env" or ".git" in full_path:
        raise HTTPException(status_code=404)
    if _CA_INDEX.exists():
        return _CA_File(str(_CA_INDEX))
    raise HTTPException(status_code=404)


@app.api_route(
    "/{full_path:path}",
    methods=["POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
def _catchall_absent_is_not_wrong_method(full_path: str, request: _CA_Request):
    """A path with no route is 404; a path with other verbs is still 405.

    The SPA catch-all above is registered for GET only. Starlette matches a
    route by path first, so an unmatched POST anywhere in the API found that
    route, failed the method check, and got **405 Method Not Allowed** --
    across the whole surface:

        POST /api/v1/ab/compare          405     (no such route)
        POST /api/v1/totally/made/up     405     (no such route)
        GET  /api/v1/totally/made/up     404

    405 says the resource exists and the verb is wrong. That is a different
    claim from "no such thing", and it sent an investigation the wrong way:
    issue #4 records `/api/v1/ab/compare` as "endpoint exists but doesn't
    accept POST -- need to add POST handler". There is no such endpoint. The
    router under /api/v1/ab has exactly predict, split and stats.

    Answering 404 unconditionally would be the same defect mirrored -- POST to
    the real GET-only `/api/v1/ab/stats` would then deny the path exists. So
    the routing table is asked: if some other route matches this path with a
    different method, the original 405 is the honest answer and is preserved.

    Registered after every include_router, so a real route for this method
    still wins on order.
    """
    from starlette.routing import Match

    scope = dict(request.scope)
    for route in app.routes:
        if getattr(route, "path", None) == "/{full_path:path}":
            continue  # the two catch-alls, including this one
        try:
            match, _ = route.matches(scope)
        except Exception:
            continue
        if match is not Match.NONE:
            # Path is real, this verb is not. That is what 405 is for.
            raise HTTPException(status_code=405, detail="Method Not Allowed")

    raise HTTPException(status_code=404, detail="Not Found")
