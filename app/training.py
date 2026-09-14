"""Loading and shaping training data. Nothing here trains or saves a model.

It used to. `retrain_pipeline()` fitted three estimators and wrote six pickles
into `models/` — the seed directory, which is a Git working tree and a container
mount at once, so a single successful run made the next deployment refuse to
start (#191). It had no callers in the repository and never ran; removing it
takes a loaded gun off the table rather than changing any behaviour.

Training now lives in `app/api/retrain.py`, which writes candidates to
`runtime/staged/<run_id>/` and leaves promotion to `app.model_source.activate`.
`tests/test_seed_is_immutable.py` refuses any new write into the seed.
"""
import logging
import os
import pandas as pd
from app.paths import data_dir

logger = logging.getLogger(__name__)

DATA_DIR = data_dir()


def load_training_data():
    try:
        from app.database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        df = pd.read_sql(text("SELECT * FROM evaluations"), db.bind)
        db.close()
        if len(df) >= 20:
            logger.info("Loaded %d rows from PostgreSQL", len(df))
            return df
        logger.warning("Only %d rows in DB, falling back to CSV", len(df))
    except Exception as e:
        logger.warning("DB load failed: %s", e)

    for name in ["sora_dataset.csv", "sora_synthetic_500.csv"]:
        p = os.path.join(DATA_DIR, name)
        if os.path.exists(p):
            df = pd.read_csv(p)
            logger.info("Loaded %d rows from %s", len(df), name)
            return df
    raise FileNotFoundError("No training data found")


def prepare_features(df):
    feature_cols = [c for c in ['budget', 'co2_reduction', 'social_impact', 'duration_months'] if c in df.columns]
    if not feature_cols:
        raise ValueError("No feature columns found")

    # Build target: use total_score median split for balanced classes
    if 'total_score' in df.columns:
        median = df['total_score'].median()
        y = (df['total_score'] >= median).astype(int)
        logger.info("Target from total_score >= %.1f median, balance: %s", median, dict(y.value_counts()))
    elif 'success_probability' in df.columns:
        median = df['success_probability'].median()
        y = (df['success_probability'] >= median).astype(int)
        logger.info("Target from success_probability >= %.1f, balance: %s", median, dict(y.value_counts()))
    elif 'success' in df.columns:
        y = df['success'].astype(int)
    else:
        raise ValueError("No target column found")

    X = df[feature_cols].copy()
    if 'budget' in X.columns and 'duration_months' in X.columns:
        X['budget_per_month'] = X['budget'] / X['duration_months'].clip(lower=1)
    if 'co2_reduction' in X.columns and 'budget' in X.columns:
        X['co2_per_dollar'] = X['co2_reduction'] / X['budget'].clip(lower=1)
    if 'social_impact' in X.columns and 'duration_months' in X.columns:
        X['impact_per_month'] = X['social_impact'] / X['duration_months'].clip(lower=1)
    X = X.fillna(0)
    return X, y
