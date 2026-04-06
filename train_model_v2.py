#!/usr/bin/env python3
import pandas as pd, numpy as np, pickle, json, os, warnings
warnings.filterwarnings("ignore")

from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier, StackingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score
from xgboost import XGBClassifier

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "data", "projects.csv")
MODELS_DIR = os.path.join(BASE_DIR, "models")

df = pd.read_csv(DATA_PATH)

# Feature engineering
df["budget_per_month"]  = df["budget"] / df["duration_months"].clip(lower=1)
df["co2_per_dollar"]    = df["co2_reduction"] / df["budget"].clip(lower=1) * 1000
df["efficiency_score"]  = (df["co2_reduction"] * df["social_impact"]) / df["duration_months"].clip(lower=1)
df["impact_ratio"]      = df["social_impact"] / df["co2_reduction"].clip(lower=1)
df["budget_efficiency"] = df["co2_reduction"] / df["budget_per_month"].clip(lower=1)

# Target encode category and region (mean of success per group)
for col in ["category", "region"]:
    mapping = df.groupby(col)["success"].mean()
    df[col + "_enc"] = df[col].map(mapping)

FEATURES = ["budget", "co2_reduction", "social_impact", "duration_months",
            "budget_per_month", "co2_per_dollar", "efficiency_score",
            "impact_ratio", "budget_efficiency",
            "category_enc", "region_enc"]

X = df[FEATURES]
y = df["success"]

scaler = StandardScaler()
scaler.fit(X)
X_scaled = pd.DataFrame(scaler.transform(X), columns=FEATURES)

cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

rf  = RandomForestClassifier(n_estimators=400, max_depth=12, min_samples_leaf=2,
                              max_features="sqrt", random_state=42)
xgb = XGBClassifier(n_estimators=400, max_depth=6, learning_rate=0.03,
                    subsample=0.8, colsample_bytree=0.7, min_child_weight=3,
                    eval_metric="logloss", random_state=42)
gb  = GradientBoostingClassifier(n_estimators=300, max_depth=4,
                                  learning_rate=0.03, subsample=0.8, random_state=42)

stacking = StackingClassifier(
    estimators=[("rf", rf), ("xgb", xgb), ("gb", gb)],
    final_estimator=LogisticRegression(C=0.5, max_iter=1000),
    cv=5, passthrough=True
)

models = {
    "RandomForest": RandomForestClassifier(n_estimators=400, max_depth=12, min_samples_leaf=2, random_state=42),
    "XGBoost": xgb,
    "Stacking": stacking,
}

print("CV AUC (5-fold):")
best_name, best_auc = None, 0
for name, model in models.items():
    scores = cross_val_score(model, X_scaled, y, cv=cv, scoring="roc_auc")
    print(f"  {name:15s} {scores.mean():.4f} +/- {scores.std():.4f}")
    if scores.mean() > best_auc:
        best_auc, best_name = scores.mean(), name

print(f"\nBest: {best_name} ({best_auc:.4f})")

# Train final model on FULL dataset
best_model = models[best_name]
best_model.fit(X_scaled, y)
y_prob = best_model.predict_proba(X_scaled)[:,1]
y_pred = best_model.predict(X_scaled)

acc = accuracy_score(y, y_pred)
f1  = f1_score(y, y_pred)
auc = roc_auc_score(y, y_prob)
print(f"Full dataset: Acc={acc:.4f}  AUC={auc:.4f}  F1={f1:.4f}")
print(f"(CV AUC = {best_auc:.4f} — use this for honest evaluation)")

# Save
with open(os.path.join(MODELS_DIR, "ensemble_model_v2.pkl"), "wb") as f:
    pickle.dump(best_model, f)

scaler_v2 = StandardScaler()
scaler_v2.fit_transform(X)
with open(os.path.join(MODELS_DIR, "scaler_v2.pkl"), "wb") as f:
    pickle.dump(scaler_v2, f)

# Save target encoding mappings
cat_mappings = {}
for col in ["category", "region"]:
    cat_mappings[col] = df.groupby(col)["success"].mean().to_dict()
with open(os.path.join(MODELS_DIR, "cat_encodings.json"), "w") as f:
    json.dump(cat_mappings, f, indent=2)

meta = {
    "best_model": best_name,
    "cv_auc": round(best_auc, 4),
    "full_dataset_auc": round(auc,),
    "accuracy": round(acc, 4),
    "f1": round(f1, 4),
    "features": FEATURES,
    "dataset_size": len(df),
    "note": "CV AUC is the honest metric. Full dataset AUC shows upper bound."
}
with open(os.path.join(MODELS_DIR, "meta_v2.json"), "w") as f:
    json.dump(meta, f, indent=2)

print("\nSaved: ensemble_model_v2.pkl, scaler_v2.pkl, cat_encodings.json, meta_v2.json")
print("Done!")
