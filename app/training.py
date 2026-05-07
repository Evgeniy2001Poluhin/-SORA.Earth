import logging
import os
import pickle
import numpy as np
import pandas as pd
from datetime import datetime
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier, StackingClassifier
from sklearn.model_selection import cross_val_score, StratifiedKFold
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score, precision_recall_curve
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
MODEL_DIR = os.path.join(ROOT_DIR, "models")
DATA_DIR = os.path.join(ROOT_DIR, "data")


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


def retrain_pipeline():
    logger.info("=== RETRAIN PIPELINE START ===")
    ts = datetime.utcnow().isoformat()

    df = load_training_data()
    X, y = prepare_features(df)
    logger.info("Features: %s, shape: %s", list(X.columns), X.shape)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

    # RandomForest
    rf = RandomForestClassifier(n_estimators=200, max_depth=10, min_samples_split=5, random_state=42, n_jobs=-1)
    rf_cv = cross_val_score(rf, X_scaled, y, cv=skf, scoring='roc_auc')
    rf.fit(X_scaled, y)

    # XGBoost / GradientBoosting
    try:
        from xgboost import XGBClassifier
        xgb = XGBClassifier(n_estimators=200, max_depth=6, learning_rate=0.1, random_state=42, eval_metric='logloss')
    except ImportError:
        xgb = GradientBoostingClassifier(n_estimators=200, max_depth=6, learning_rate=0.1, random_state=42)
    xgb_cv = cross_val_score(xgb, X_scaled, y, cv=skf, scoring='roc_auc')
    xgb.fit(X_scaled, y)

    # Stacking with StratifiedKFold
    ensemble = StackingClassifier(
        estimators=[('rf', rf), ('xgb', xgb)],
        final_estimator=GradientBoostingClassifier(n_estimators=50, random_state=42),
        cv=StratifiedKFold(n_splits=3, shuffle=True, random_state=42),
        passthrough=True
    )
    ens_cv = cross_val_score(ensemble, X_scaled, y, cv=StratifiedKFold(n_splits=3, shuffle=True, random_state=42), scoring='roc_auc')
    ensemble.fit(X_scaled, y)

    # Optimal threshold
    y_prob = rf.predict_proba(X_scaled)[:, 1]
    precisions, recalls, thresholds = precision_recall_curve(y, y_prob)
    f1_scores = 2 * (precisions * recalls) / (precisions + recalls + 1e-8)
    best_idx = np.argmax(f1_scores)
    best_threshold = float(thresholds[best_idx]) if best_idx < len(thresholds) else 0.5

    y_pred = (y_prob >= best_threshold).astype(int)
    metrics = {
        "timestamp": ts,
        "samples": len(df),
        "features": list(X.columns),
        "target_balance": {int(k): int(v) for k, v in y.value_counts().items()},
        "rf_cv_auc": round(float(rf_cv.mean()), 4),
        "rf_cv_std": round(float(rf_cv.std()), 4),
        "xgb_cv_auc": round(float(xgb_cv.mean()), 4),
        "xgb_cv_std": round(float(xgb_cv.std()), 4),
        "ensemble_cv_auc": round(float(ens_cv.mean()), 4),
        "best_threshold": round(best_threshold, 4),
        "train_accuracy": round(float(accuracy_score(y, y_pred)), 4),
        "train_f1": round(float(f1_score(y, y_pred)), 4),
        "train_auc": round(float(roc_auc_score(y, y_prob)), 4),
    }

    os.makedirs(MODEL_DIR, exist_ok=True)
    for name, obj in [("rf_model.pkl", rf), ("xgb_model.pkl", xgb), ("ensemble_model.pkl", ensemble), ("scaler.pkl", scaler), ("best_threshold.pkl", {"threshold": best_threshold}), ("retrain_metrics.pkl", metrics)]:
        with open(os.path.join(MODEL_DIR, name), "wb") as f:
            pickle.dump(obj, f)

    logger.info("=== RETRAIN COMPLETE: RF=%.4f XGB=%.4f ENS=%.4f ===", metrics["rf_cv_auc"], metrics["xgb_cv_auc"], metrics["ensemble_cv_auc"])
    return metrics
