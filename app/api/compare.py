import os
import pickle
from datetime import datetime as _dt

import pandas as pd
from fastapi import APIRouter
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score

router = APIRouter(prefix="/model", tags=["ml-ops"])

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODELS = os.path.join(ROOT_DIR, "models")
DATA_CSV = os.path.join(ROOT_DIR, "data", "projects.csv")

SCALER_COLS = [
    "budget", "co2_reduction", "social_impact", "duration_months",
    "budget_per_month", "co2_per_dollar", "efficiency_score", "year", "quarter",
]


def _load_model(rf_path, sc_path):
    if not os.path.exists(rf_path) or not os.path.exists(sc_path):
        return None, None
    with open(rf_path, "rb") as f:
        rf = pickle.load(f)
    with open(sc_path, "rb") as f:
        sc = pickle.load(f)
    return rf, sc


def _prepare_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["budget_per_month"] = df["budget"] / df["duration_months"].clip(lower=1)
    df["co2_per_dollar"] = df["co2_reduction"] / df["budget"].clip(lower=1) * 1000
    derived = (df["co2_reduction"] * df["social_impact"]) / df["duration_months"].clip(lower=1)
    df["efficiency_score"] = derived
    df["impact_per_month"] = derived
    now = _dt.utcnow()
    df["year"] = now.year
    df["quarter"] = (now.month - 1) // 3 + 1
    return df



def _coerce_features_for_model(rf, X):
    """Подгоняет матрицу признаков X под ожидаемое rf.n_features_in_."""
    import numpy as np
    n_need = getattr(rf, "n_features_in_", X.shape[1])
    n_have = X.shape[1]
    if n_have == n_need:
        return X
    if n_have > n_need:
        return X[:, :n_need]
    pad = np.zeros((X.shape[0], n_need - n_have))
    return np.hstack([X, pad])

def _score(rf, sc):
    df = pd.read_csv(DATA_CSV)
    df = _prepare_features(df)

    n_need = rf.n_features_in_
    rf_cols = getattr(rf, "feature_names_in_", None)
    sc_cols = getattr(sc, "feature_names_in_", None) if sc else None

    if rf_cols is not None:
        model_cols = list(rf_cols)
    elif sc_cols is not None and len(sc_cols) == n_need:
        model_cols = list(sc_cols)
    else:
        model_cols = SCALER_COLS[:n_need]

    for c in model_cols:
        if c not in df.columns:
            df[c] = 0

    X = df[model_cols].fillna(0).values

    if sc is not None:
        if sc_cols is not None:
            sc_n = len(sc_cols)
        else:
            sc_n = getattr(sc, "n_features_in_", X.shape[1])

        if sc_n == X.shape[1]:
            X = sc.transform(X)
        else:
            tmp = df[list(sc_cols)].fillna(0).values if sc_cols is not None else X[:, :sc_n]
            tmp = sc.transform(tmp)
            col_map = {name: i for i, name in enumerate(sc_cols)} if sc_cols is not None else {}
            import numpy as np
            result = np.zeros((X.shape[0], n_need))
            for i, col_name in enumerate(model_cols):
                if col_name in col_map:
                    result[:, i] = tmp[:, col_map[col_name]]
                else:
                    result[:, i] = df[col_name].fillna(0).values
            X = result

    col = "success" if "success" in df.columns else "approved"
    y = df[col].values
    preds = rf.predict(X)
    proba = rf.predict_proba(X)[:, 1]

    return {
        "auc": round(float(roc_auc_score(y, proba)), 4),
        "f1": round(float(f1_score(y, preds)), 4),
        "accuracy": round(float(accuracy_score(y, preds)), 4),
        "n_estimators": rf.n_estimators,
        "n_features": rf.n_features_in_,
    }


@router.get("/compare")
def compare_models():
    cur_rf, cur_sc = _load_model(
        os.path.join(MODELS, "random_forest.pkl"),
        os.path.join(MODELS, "scaler.pkl"),
    )
    bak_rf, bak_sc = _load_model(
        os.path.join(MODELS, "random_forest.pkl.bak"),
        os.path.join(MODELS, "scaler.pkl.bak"),
    )

    result = {"current": None, "backup": None, "winner": None, "delta": {}}

    if cur_rf:
        result["current"] = _score(cur_rf, cur_sc)
    if bak_rf:
        result["backup"] = _score(bak_rf, bak_sc)

    if result["current"] and result["backup"]:
        for m in ["auc", "f1", "accuracy"]:
            result["delta"][m] = round(result["current"][m] - result["backup"][m], 4)
        result["winner"] = "current" if result["current"]["auc"] >= result["backup"]["auc"] else "backup"
    elif result["current"]:
        result["winner"] = "current"
        result["backup"] = "no_backup_found"

    return result
