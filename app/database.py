from datetime import datetime
import os

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine
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


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
