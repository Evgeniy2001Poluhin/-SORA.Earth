from datetime import datetime
import os

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text, create_engine, func
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{os.path.join(os.path.dirname(__file__), '..', 'data', 'sora.db')}"
)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


class Evaluation(Base):
    __tablename__ = "evaluations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=True)
    budget = Column(Float)
    co2_reduction = Column(Float)
    social_impact = Column(Float)
    duration_months = Column(Integer)
    total_score = Column(Float)
    environment_score = Column(Float)
    social_score = Column(Float)
    economic_score = Column(Float)
    success_probability = Column(Float)
    recommendation = Column(Text)
    risk_level = Column(String(50))
    region = Column(String(100), default="Europe")
    lat = Column(Float, default=50.0)
    lon = Column(Float, default=10.0)
    created_at = Column(DateTime, default=datetime.utcnow)


class PredictionLog(Base):
    __tablename__ = "predictions_log"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    request_id = Column(String(64), nullable=True)
    endpoint = Column(String(50))
    model_version = Column(String(50), default="v2.0")
    budget = Column(Float)
    co2_reduction = Column(Float)
    social_impact = Column(Float)
    duration_months = Column(Integer)
    category = Column(String(100), nullable=True)
    region = Column(String(100), nullable=True)
    prediction = Column(Integer, nullable=True)
    probability = Column(Float, nullable=True)
    esg_total_score = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    latency_ms = Column(Float, nullable=True)


class DataRefreshLog(Base):
    __tablename__ = "data_refresh_log"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String(50), nullable=True)
    country_iso3 = Column(String(10), nullable=True)
    indicator = Column(String(100), nullable=True)
    value = Column(Float, nullable=True)
    error_message = Column(Text, nullable=True)
    fetched_at = Column(DateTime, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    job_name = Column(String(100), default="external_data_refresh")
    status = Column(String(50), nullable=False)
    countries_fetched = Column(Integer, default=0)
    total_countries = Column(Integer, default=0)
    message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    duration_sec = Column(Float, nullable=True)
    trigger_source = Column(String(50), nullable=True, default="manual")


class CountryIndicatorHistory(Base):
    __tablename__ = "country_indicator_history"

    id = Column(Integer, primary_key=True, index=True)
    country_iso3 = Column(String(10), nullable=False, index=True)
    country_name = Column(String(100), nullable=True, index=True)
    indicator_code = Column(String(100), nullable=False, index=True)
    indicator_name = Column(String(150), nullable=True)
    value = Column(Float, nullable=True)
    source = Column(String(50), nullable=False, default="unknown", index=True)
    as_of_date = Column(DateTime, nullable=True, index=True)
    fetched_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    refresh_job_name = Column(String(100), nullable=True)


class RetrainLog(Base):
    __tablename__ = "retrain_log"

    id = Column(Integer, primary_key=True, index=True)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    finished_at = Column(DateTime, nullable=True, index=True)
    duration_sec = Column(Float, nullable=True)
    status = Column(String(50), nullable=False, index=True)
    trigger_source = Column(String(50), default="scheduler", index=True)
    job_name = Column(String(100), default="model_retrain", nullable=True)
    model_version = Column(String(100), nullable=True)
    data_version = Column(String(100), nullable=True)
    metrics_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    message = Column(Text, nullable=True)




class BatchResultDB(Base):
    __tablename__ = "batch_results"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(String(64), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    job_type = Column(String(50), nullable=False, default="batch_evaluate")
    total = Column(Integer, nullable=False)
    successful = Column(Integer, nullable=False, default=0)
    failed = Column(Integer, nullable=False, default=0)
    duration_ms = Column(Float, nullable=True)
    status = Column(String(50), nullable=False, default="completed")
    error_message = Column(Text, nullable=True)
    results_json = Column(Text, nullable=True)
    trigger_source = Column(String(50), nullable=False, default="manual")


def init_db():
    Base.metadata.create_all(bind=engine, checkfirst=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class RegionSignal(Base):
    __tablename__ = "region_signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    region_code = Column(String(10), index=True, nullable=False)
    source = Column(String(32), index=True, nullable=False)
    metric = Column(String(64), nullable=False)
    value = Column(Float, nullable=True)
    unit = Column(String(32), nullable=True)
    observed_at = Column(DateTime(timezone=True), index=True, nullable=True)
    created_at = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    metadata_json = Column(Text, nullable=True)

class RegionESGScore(Base):
    __tablename__ = "region_esg_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    region_code = Column(String(10), index=True, unique=True, nullable=False)
    env_score = Column(Float, nullable=True)
    social_score = Column(Float, nullable=True)
    gov_score = Column(Float, nullable=True)
    total_score = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    sources_count = Column(Integer, nullable=True)
    signals_used = Column(Integer, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=func.now(),
                        onupdate=func.now(), nullable=False)



class WebhookSubscription(Base):
    __tablename__ = "webhook_subscriptions"
    id = Column(Integer, primary_key=True)
    url = Column(String(512), nullable=False)
    event_type = Column(String(64), nullable=False, default="drift")
    secret = Column(String(128), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    id = Column(Integer, primary_key=True)
    subscription_id = Column(Integer, index=True)
    event_type = Column(String(64))
    status_code = Column(Integer)
    ok = Column(Boolean, default=False)
    error = Column(String(512))
    created_at = Column(DateTime, default=datetime.utcnow)


class HealthPing(Base):
    __tablename__ = "health_pings"
    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, default=datetime.utcnow, index=True)
    component = Column(String(32), index=True)
    ok = Column(Boolean, default=True)
