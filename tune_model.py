
import optuna, pickle, json, os, warnings, pandas as pd, numpy as np
warnings.filterwarnings("ignore")
optuna.logging.set_verbosity(optuna.logging.WARNING)

from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import StackingClassifier, RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from xgboost import XGBClassifier

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
df = pd.read_csv(os.path.join(BASE_DIR, "data", "projects.csv"))

df["budget_per_month"]  = df["budget"] / df["duration_months"].clip(lower=1)
df["co2_per_dollar"]    = df["co2_reduction"] / df["budget"].clip(lower=1) * 1000
df["efficiency_score"]  = (df["co2_reduction"] * df["social_impact"]) / df["duration_months"].clip(lower=1)
df["impact_ratio"]      = df["social_impact"] / df["co2_reduction"].clip(lower=1)
df["budget_efficiency"] = df["co2_reduction"] / df["budget_per_month"].clip(lower=1)
for col in ["category", "region"]:
    df[col+"_enc"] = df[col].map(df.groupby(col)["success"].mean())

FEATURES = ["budget","co2_reduction","social_impact","duration_months",
            "budget_per_month","co2_per_dollar","efficiency_score",
            "impact_ratio","budget_efficiency","category_enc","region_enc"]

X = pd.DataFrame(StandardScaler().fit_transform(df[FEATURES]), columns=FEATURES)
y = df["success"]
cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

def objective(trial):
    xgb = XGBClassifier(
        n_estimators   = trial.suggest_int("xgb_n", 200, 600),
        max_depth      = trial.suggest_int("xgb_depth", 3, 8),
        learning_rate  = trial.suggest_float("xgb_lr", 0.01, 0.1, log=True),
        subsample      = trial.suggest_float("xgb_sub", 0.6, 1.0),
        colsample_bytree = trial.suggest_float("xgb_col", 0.5, 1.0),
        min_child_weight = trial.suggest_int("xgb_mcw", 1, 10),
        eval_metric="logloss", random_state=42
    )
    rf = RandomForestClassifier(
        n_estimators  = trial.suggest_int("rf_n", 200, 500),
        max_depth     = trial.suggest_int("rf_depth", 6, 16),
        min_samples_leaf = trial.suggest_int("rf_leaf", 1, 5),
        max_features  = trial.suggest_categorical("rf_feat", ["sqrt", "log2"]),
        random_state=42
    )
    gb = GradientBoostingClassifier(
        n_estimators  = trial.suggest_int("gb_n", 150, 400),
        max_depth     = trial.suggest_int("gb_depth", 3, 6),
        learning_rate = trial.suggest_float("gb_lr", 0.01, 0.1, log=True),
        subsample     = trial.suggest_float("gb_sub", 0.6, 1.0),
        random_state=42
    )
    model = StackingClassifier(
        estimators=[("rf",rf),("xgb",xgb),("gb",gb)],
        final_estimator=LogisticRegression(C=trial.suggest_float("lr_C",0.01,10,log=True), max_iter=1000),
        cv=3, passthrough=True
    )
    scores = cross_val_score(model, X, y, cv=cv, scoring="roc_auc")
    return scores.mean()

print("Running Optuna (40 trials)...")
study = optuna.create_study(direction="maximize")
study.optimize(objective, n_trials=40, n_jobs=-1, show_progress_bar=True)

print(f"Best CV AUC: {study.best_value:.4f}")
print(f"Best params: {study.best_params}")

# Train final model with best params
p = study.best_params
xgb = XGBClassifier(n_estimators=p["xgb_n"], max_depth=p["xgb_depth"],
    learning_rate=p["xgb_lr"], subsample=p["xgb_sub"],
    colsample_bytree=p["xgb_col"], min_child_weight=p["xgb_mcw"],
    eval_metric="logloss", random_state=42)
rf  = RandomForestClassifier(n_estimators=p["rf_n"], max_depth=p["rf_depth"],
    min_samples_leaf=p["rf_leaf"], max_features=p["rf_feat"], random_state=42)
gb  = GradientBoostingClassifier(n_estimators=p["gb_n"], max_depth=p["gb_depth"],
    learning_rate=p["gb_lr"], subsample=p["gb_sub"], random_state=42)
final = StackingClassifier(
    estimators=[("rf",rf),("xgb",xgb),("gb",gb)],
    final_estimator=LogisticRegression(C=p["lr_C"], max_iter=1000),
    cv=5, passthrough=True
)
final.fit(X, y)

MODELS_DIR = os.path.join(BASE_DIR, "models")
with open(os.path.join(MODELS_DIR, "ensemble_model_v3.pkl"), "wb") as f:
    pickle.dump(final, f)
with open(os.path.join(MODELS_DIR, "optuna_best_params.json"), "w") as f:
    json.dump({"best_cv_auc": round(study.best_value,4), "params": p}, f, indent=2)

print("Saved: ensemble_model_v3.pkl, optuna_best_params.json")
