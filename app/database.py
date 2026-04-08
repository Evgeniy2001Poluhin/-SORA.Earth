"""Database layer — SQLAlchemy + supports SQLite (dev) and PostgreSQL (prod)."""
import os
from sqlalchemy import create_engine, Column, Integer, Float, String, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{os.path.join(os.path.dirname(__file__), '..', 'data', 'sora.db')}")

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


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
