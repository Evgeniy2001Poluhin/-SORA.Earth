#!/usr/bin/env python3
"""SORA.Earth — MLOps training pipeline with Registry."""
import os, json, pickle, warnings, subprocess, tempfile
warnings.filterwarnings("ignore")

import pandas as pd
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier, HistGradientBoostingClassifier, StackingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from xgboost import XGBClassifier

import mlflow
import mlflow.sklearn
from mlflow.tracking import MlflowClient
from mlflow.models.signature import infer_signature

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "data", "projects.csv")
MODELS_DIR = os.path.join(BASE_DIR, "models")
os.makedirs(MODELS_DIR, exist_ok=True)

TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://7.0.0.1:5555")
EXPERIMENT = "esg-success-classification-v2"
REGISTERED_MODEL = "esg-success-predictor"
PROMOTION_AUC_THRESHOLD = 0.80
PRODUCTION_AUC_THRESHOLD = 0.85

mlflow.set_tracking_uri(TRACKING_URI)
mlflow.set_experiment(EXPERIMENT)
client = MlflowClient()


def _git_sha():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode().strip()
    except Exception:
        return "nogit"


def _serializable_params(model):
    out = {}
    for k, v in model.get_params().items():
        if isinstance(v, (int, float, str, bool)) or v is None:
            out[k] = v
    return out


df = pd.read_csv(DATA_PATH)
before = len(df)
df = df.dropna(subset=["success"]).reset_index(drop=True)
df["success"] = df["success"].astype(int)
print("Rows:", len(df))
before = len(df)
df = df.dropna(subset=["success"]).reset_index(drop=True)
df["success"] = df["success"].astype(int)
print("Dropped", before - len(df), "rows with NaN success;", len(df), "remain")
df["budget_per_month"] = df["budget"] / df["duration_months"].clip(lower=1)
df["co2_per_dollar"] = df["co2_reduction"] / df["budget"].clip(lower=1) * 1000
df["efficiency_score"] = (df["co2_reduction"] * df["social_impact"]) / df["duration_months"].clip(lower=1)
df["impact_ratio"] = df["social_impact"] / df["co2_reduction"].clip(lower=1)
df["budget_efficiency"] = df["co2_reduction"] / df["budget_per_month"].clip(lower=1)

cat_mappings = {}
for col in ["category", "region"]:
    m = df.groupby(col)["success"].mean()
    cat_mappings[col] = m.to_dict()
    df[col + "_enc"] = df[col].map(m)

FEATURES = ["budget","co2_reduction","social_impact","duration_months",
            "budget_per_month","co2_per_dollar","efficiency_score",
            "impact_ratio","budget_efficiency","category_enc","region_enc"]
X = df[FEATURES]
y = df["success"]
scaler = StandardScaler().fit(X)
X_scaled = pd.DataFrame(scaler.transform(X), columns=FEATURES)

cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
rf = RandomForestClassifier(n_estimators=400, max_depth=12, min_samples_leaf=2,
                            max_features="sqrt", random_state=42)
xgbm = XGBClassifier(n_estimators=400, max_depth=6, learning_rate=0.03,
                     subsample=0.8, colsample_bytree=0.7, min_child_weight=3,
                     eval_metric="logloss", random_state=42)
gb = HistGradientBoostingClassifier(max_iter=300, max_depth=4, learning_rate=0.05, random_state=42)
stacking = StackingClassifier(
    estimators=[("rf", rf), ("xgb", xgbm), ("gb", gb)],
    final_estimator=LogisticRegression(C=0.5, max_iter=1000),
    cv=5, passthrough=True,
)
MODELS = {"RandomForest": rf, "XGBoost": xgbm, "Stacking": stacking}

results = {}
git_sha = _git_sha()

with mlflow.start_run(run_name="compare-" + git_sha) as parent:
    mlflow.set_tag("git.sha", git_sha)
    mlflow.set_tag("dataset", "projects.csv")
    mlflow.log_param("n_samples", len(df))
    mlflow.log_param("n_features", len(FEATURES))
    mlflow.log_dict({"features": FEATURES}, "features.json")
    mlflow.log_dict(cat_mappings, "cat_encodings.json")

    for name, model in MODELS.items():
        with mlflow.start_run(run_name=name, nested=True) as child:
            mlflow.set_tag("model_family", name)
            mlflow.set_tag("git.sha", git_sha)
            mlflow.log_params(_serializable_params(model))

            cv_auc = cross_val_score(model, X_scaled, y, cv=cv, scoring="roc_auc")
            cv_acc = cross_val_score(model, X_scaled, y, cv=cv, scoring="accuracy")
            cv_f1 = cross_val_score(model, X_scaled, y, cv=cv, scoring="f1")
            mlflow.log_metric("cv_roc_auc_mean", cv_auc.mean())
            mlflow.log_metric("cv_roc_auc_std", cv_auc.std())
            mlflow.log_metric("cv_accuracy", cv_acc.mean())
            mlflow.log_metric("cv_f1", cv_f1.mean())

            model.fit(X_scaled, y)
            proba = model.predict_proba(X_scaled)[:, 1]
            mlflow.log_metric("train_set_auc_overfit", roc_auc_score(y, proba))

            sig = infer_signature(X_scaled, proba)
            mlflow.sklearn.log_model(model, "model",
                                     signature=sig, input_example=X_scaled.head(3))

            results[name] = {"run_id": child.info.run_id, "cv_auc": cv_auc.mean(),
                             "cv_auc_std": cv_auc.std(), "model": model}
            print(name.ljust(15), "CV AUC =", round(cv_auc.mean(), 4),
                  "+/-", round(cv_auc.std(), 4))

    best_name = max(results, key=lambda n: results[n]["cv_auc"])
    best = results[best_name]
    mlflow.log_param("best_model", best_name)
    mlflow.log_metric("best_cv_auc", best["cv_auc"])

    with tempfile.TemporaryDirectory() as tmp:
        sp = os.path.join(tmp, "scaler.pkl")
        with open(sp, "wb") as f:
            pickle.dump(scaler, f)
        mlflow.log_artifact(sp, "preprocessor")
        cp = os.path.join(tmp, "cat_encodings.json")
        with open(cp, "w") as f:
            json.dump(cat_mappings, f, indent=2)
        mlflow.log_artifact(cp, "preprocessor")

print()
print("=== MODEL REGISTRY ===")
best_run_id = best["run_id"]
best_auc = best["cv_auc"]
model_uri = "runs:/" + best_run_id + "/model"

try:
    client.create_registered_model(REGISTERED_MODEL)
    print("Created registered model:", REGISTERED_MODEL)
except Exception:
    print("Registered model exists:", REGISTERED_MODEL)

mv = client.create_model_version(
    name=REGISTERED_MODEL,
    source=model_uri,
    run_id=best_run_id,
    tags={"algo": best_name, "git.sha": git_sha, "cv_auc": str(round(best_auc, 4))},
    description=best_name + " | CV AUC " + str(round(best_auc, 4)) + " | git " + git_sha,
)
print("Created version", mv.version, "of", REGISTERED_MODEL)

if best_auc >= PRODUCTION_AUC_THRESHOLD:
    target_stage = "Production"
elif best_auc >= PROMOTION_AUC_THRESHOLD:
    target_stage = "Staging"
else:
    target_stage = None

if target_stage:
    client.transition_model_version_stage(
        name=REGISTERED_MODEL, version=mv.version,
        stage=target_stage, archive_existing_versions=True,
    )
    print("Transitioned version", mv.version, "->", target_stage)
else:
    print("AUC", round(best_auc, 4), "below threshold", PROMOTION_AUC_THRESHOLD, "- no promotion")

with open(os.path.join(MODELS_DIR, "ensemble_model_v2.pkl"), "wb") as f:
    pickle.dump(best["model"], f)
with open(os.path.join(MODELS_DIR, "scaler_v2.pkl"), "wb") as f:
    pickle.dump(scaler, f)
with open(os.path.join(MODELS_DIR, "cat_encodings.json"), "w") as f:
    json.dump(cat_mappings, f, indent=2)
with open(os.path.join(MODELS_DIR, "meta_v2.json"), "w") as f:
    json.dump({
        "best_model": best_name,
        "cv_auc": round(best_auc, 4),
        "cv_auc_std": round(best["cv_auc_std"], 4),
        "features": FEATURES,
        "dataset_size": len(df),
        "mlflow_run_id": best_run_id,
        "registered_model": REGISTERED_MODEL,
        "version": mv.version,
        "stage": target_stage or "None",
    }, f, indent=2)

print()
print("Best:", best_name, "| CV AUC =", round(best_auc, 4))
print("Run:", best_run_id)
print("Registry:", REGISTERED_MODEL, "v" + str(mv.version), "->", target_stage or "None")
print("UI: https://mlflow.sora-earth.ru/#/models/" + REGISTERED_MODEL)
