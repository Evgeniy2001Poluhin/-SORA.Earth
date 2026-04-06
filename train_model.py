#!/usr/bin/env python3
"""
SORA.Earth — Model Training Pipeline
"""
import pandas as pd
import numpy as np
import pickle
import json
import os
import warnings
warnings.filterwarnings("ignore")

from sklearn.model_selection import train_test_split, cross_val_score, StratifiedKFold
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, precision_score, recall_score,
                             f1_score, roc_auc_score, confusion_matrix, roc_curve)
from xgboost import XGBClassifier
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "data", "projects.csv")
MODELS_DIR = os.path.join(BASE_DIR, "models")
PLOTS_DIR = os.path.join(BASE_DIR, "plots")
os.makedirs(MODELS_DIR, exist_ok=True)
os.makedirs(PLOTS_DIR, exist_ok=True)

print("=" * 60)
print("SORA.Earth Model Training Pipeline")
print("=" * 60)

df = pd.read_csv(DATA_PATH)
print(f"\nDataset: {len(df)} projects")
print(f"Success rate: {df['success'].mean()*100:.1f}%")
print(f"\nCategory distribution:\n{df['category'].value_counts().to_string()}")
print(f"\nRegion distribution:\n{df['region'].value_counts().to_string()}")
print(f"\nStats:\n{df[['budget','co2_reduction','social_impact','duration_months']].describe().round(1).to_string()}")

# Feature engineering — должно совпадать с make_features() в app/main.py
df["budget_per_month"] = df["budget"] / df["duration_months"].clip(lower=1)
df["co2_per_dollar"]   = df["co2_reduction"] / df["budget"].clip(lower=1) * 1000
df["efficiency_score"] = (df["co2_reduction"] * df["social_impact"]) / df["duration_months"].clip(lower=1)

FEATURES = ["budget", "co2_reduction", "social_impact", "duration_months",
            "budget_per_month", "co2_per_dollar", "efficiency_score"]
X = df[FEATURES]
y = df["success"]

scaler = StandardScaler()
scaler.fit(X)
X_scaled = pd.DataFrame(scaler.transform(X), columns=FEATURES)

X_train, X_test, y_train, y_test = train_test_split(
    X_scaled, y, test_size=0.2, random_state=42, stratify=y)
print(f"\nTrain: {len(X_train)}, Test: {len(X_test)}")

models = {
    "RandomForest": RandomForestClassifier(n_estimators=200, max_depth=10, random_state=42),
    "XGBoost": XGBClassifier(n_estimators=200, max_depth=6, learning_rate=0.1,
                              use_label_encoder=False, eval_metric="logloss", random_state=42),
    "GradientBoosting": GradientBoostingClassifier(n_estimators=200, max_depth=5, random_state=42),
    "LogisticRegression": LogisticRegression(max_iter=1000, random_state=42),
}

print("\n" + "=" * 60)
print("Cross-Validation (5-fold)")
print("=" * 60)

cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
cv_results = {}
for name, model in models.items():
    scores = cross_val_score(model, X_scaled, y, cv=cv, scoring="accuracy")
    cv_results[name] = {"accuracy": round(scores.mean()*100, 1), "std": round(scores.std()*100, 2)}
    print(f"{name:25s} -> {scores.mean()*100:.1f}% +/- {scores.std()*100:.2f}%")

print("\n" + "=" * 60)
print("Test Set Evaluation")
print("=" * 60)

test_metrics = {}
trained_models = {}
for name, model in models.items():
    model.fit(X_train, y_train)
    trained_models[name] = model
    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]
    acc = round(accuracy_score(y_test, y_pred)*100, 2)
    prec = round(precision_score(y_test, y_pred)*100, 2)
    rec = round(recall_score(y_test, y_pred)*100, 2)
    f1 = round(f1_score(y_test, y_pred)*100, 2)
    auc = round(roc_auc_score(y_test, y_proba)*100, 2)
    test_metrics[name] = {"accuracy": acc, "precision": prec, "recall": rec, "f1": f1, "roc_auc": auc}
    print(f"\n{name}: Acc={acc}% Prec={prec}% Rec={rec}% F1={f1}% AUC={auc}%")
    print(f"  CM:\n{confusion_matrix(y_test, y_pred)}")

best_name = max(cv_results, key=lambda k: cv_results[k]["accuracy"])
best_model = trained_models[best_name]
print(f"\n{'='*60}\nBest: {best_name} ({cv_results[best_name]['accuracy']}% CV)\n{'='*60}")

if hasattr(best_model, "feature_importances_"):
    fi = best_model.feature_importances_
    fi_pct = (fi / fi.sum() * 100).round(1)
    fi_dict = dict(zip(FEATURES, fi_pct.tolist()))
    print("\nFeature Importance:")
    for f, v in sorted(fi_dict.items(), key=lambda x: -x[1]):
        print(f"  {f:20s} {v}%")
else:
    fi_dict = {f: 25.0 for f in FEATURES}

with open(os.path.join(MODELS_DIR, "model.pkl"), "wb") as f:
    pickle.dump(trained_models["RandomForest"], f)
with open(os.path.join(MODELS_DIR, "xgb_model.pkl"), "wb") as f:
    pickle.dump(trained_models["XGBoost"], f)
with open(os.path.join(MODELS_DIR, "scaler.pkl"), "wb") as f:
    pickle.dump(scaler, f)

meta = {
    "best_model": best_name,
    "accuracy": cv_results[best_name]["accuracy"],
    "all_results": cv_results,
    "feature_importance": fi_dict,
    "dataset_size": len(df),
    "success_rate": round(df["success"].mean()*100, 1),
    "features": FEATURES,
    "test_size": 0.2,
    "random_state": 42
}
with open(os.path.join(MODELS_DIR, "meta.json"), "w") as f:
    json.dump(meta, f, indent=2)

metrics_out = {k: test_metrics[k] for k in ["RandomForest", "XGBoost"]}
with open(os.path.join(MODELS_DIR, "metrics.json"), "w") as f:
    json.dump(metrics_out, f, indent=2)

# --- PLOTS ---
plt.figure(figsize=(8, 6))
for name, model in trained_models.items():
    y_proba = model.predict_proba(X_test)[:, 1]
    fpr, tpr, _ = roc_curve(y_test, y_proba)
    auc = roc_auc_score(y_test, y_proba)
    plt.plot(fpr, tpr, label=f"{name} (AUC={auc:.3f})", linewidth=2)
plt.plot([0, 1], [0, 1], "k--", alpha=0.3)
plt.xlabel("False Positive Rate")
plt.ylabel("True Positive Rate")
plt.title("ROC Curves")
plt.legend()
plt.grid(alpha=0.3)
plt.tight_layout()
plt.savefig(os.path.join(PLOTS_DIR, "roc_curves.png"), dpi=150)
plt.close()

if hasattr(best_model, "feature_importances_"):
    plt.figure(figsize=(8, 5))
    sorted_idx = np.argsort(best_model.feature_importances_)
    plt.barh([FEATURES[i] for i in sorted_idx], best_model.feature_importances_[sorted_idx],
             color=["#00e5a0", "#00c2ff", "#8b5cf6", "#eab308"])
    plt.xlabel("Importance")
    plt.title(f"Feature Importance ({best_name})")
    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, "feature_importance.png"), dpi=150)
    plt.close()

fig, axes = plt.subplots(1, 4, figsize=(20, 4))
for ax, (name, model) in zip(axes, trained_models.items()):
    y_pred = model.predict(X_test)
    cm = confusion_matrix(y_test, y_pred)
    sns.heatmap(cm, annot=True, fmt="d", cmap="Blues", ax=ax,
                xticklabels=["Fail", "Success"], yticklabels=["Fail", "Success"])
    ax.set_title(f"{name}\n(Acc: {test_metrics[name]['accuracy']}%)")
    ax.set_xlabel("Predicted")
    ax.set_ylabel("Actual")
plt.tight_layout()
plt.savefig(os.path.join(PLOTS_DIR, "confusion_matrices.png"), dpi=150)
plt.close()

fig, ax = plt.subplots(figsize=(10, 5))
metric_names = ["accuracy", "precision", "recall", "f1", "roc_auc"]
x = np.arange(len(metric_names))
width = 0.2
for i, (name, m) in enumerate(test_metrics.items()):
    vals = [m[k] for k in metric_names]
    ax.bar(x + i*width, vals, width, label=name)
ax.set_xticks(x + width*1.5)
ax.set_xticklabels(["Accuracy", "Precision", "Recall", "F1", "ROC-AUC"])
ax.set_ylim(50, 100)
ax.set_ylabel("Score (%)")
ax.set_title("Model Comparison")
ax.legend()
ax.grid(axis="y", alpha=0.3)
plt.tight_layout()
plt.savefig(os.path.join(PLOTS_DIR, "model_comparison.png"), dpi=150)
plt.close()

fig, axes = plt.subplots(2, 2, figsize=(12, 10))
for ax, col in zip(axes.flat, FEATURES):
    ax.hist(df[df["success"]==1][col], bins=15, alpha=0.7, label="Success", color="#00e5a0")
    ax.hist(df[df["success"]==0][col], bins=15, alpha=0.7, label="Fail", color="#ef4444")
    ax.set_title(col)
    ax.legend()
plt.suptitle("Feature Distribution by Outcome", fontsize=14)
plt.tight_layout()
plt.savefig(os.path.join(PLOTS_DIR, "data_distribution.png"), dpi=150)
plt.close()

plt.figure(figsize=(8, 6))
corr = df[FEATURES + ["success"]].corr()
sns.heatmap(corr, annot=True, cmap="coolwarm", center=0, fmt=".2f")
plt.title("Correlation Matrix")
plt.tight_layout()
plt.savefig(os.path.join(PLOTS_DIR, "correlation_matrix.png"), dpi=150)
plt.close()

print(f"\nPlots saved to {PLOTS_DIR}/")
print(f"Models saved to {MODELS_DIR}/")
print("\nDone!")
