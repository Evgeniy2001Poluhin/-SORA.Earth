#!/usr/bin/env python3
"""Retrain the v2 model with Optuna hyperparameter search for maximum AUC.

Tunes an XGBoost classifier over `--trials` Optuna trials (objective = 3-fold
stratified CV ROC-AUC), fits the best config on the train split, calibrates it,
and saves to the standard model paths so it drops into the app's loader.

Usage:
    python3 scripts/retrain_ensemble_optuna.py --data data/projects_enriched.csv --trials 50

Feature set:
    The 11 serving features (matching app.main.FEATURE_COLS_V2) plus, when present
    and --gdp is on (default), country_gdp_per_capita.

    ⚠️  country_gdp_per_capita is NOT available at inference time — the /predict
    request has no country field and app.make_features_v2 builds only the 11
    serving features. A model trained with --gdp therefore cannot be served by
    the current /predict/stacking path without (a) adding a country input to the
    predict API + a country->GDP lookup, or (b) retraining with --no-gdp.
    Use --gdp to measure the AUC ceiling; use --no-gdp for a drop-in model.
"""
import argparse
import json
import os
import pickle

import numpy as np
import pandas as pd
import optuna
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.preprocessing import StandardScaler

try:
    from xgboost import XGBClassifier
    _HAS_XGB = True
except ImportError:  # pragma: no cover
    from sklearn.ensemble import GradientBoostingClassifier
    _HAS_XGB = False

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

BASE_COLS = ["budget", "co2_reduction", "social_impact", "duration_months",
             "budget_per_month", "co2_per_dollar", "efficiency_score",
             "impact_ratio", "budget_efficiency", "category_enc", "region_enc"]


def engineer(df):
    df["budget_per_month"] = df["budget"] / df["duration_months"].clip(lower=1)
    df["co2_per_dollar"] = df["co2_reduction"] / df["budget"].clip(lower=1) * 1000
    df["efficiency_score"] = (df["co2_reduction"] * df["social_impact"]) / df["duration_months"].clip(lower=1)
    df["impact_ratio"] = df["co2_reduction"] / (df["social_impact"] + 1)
    df["budget_efficiency"] = df["co2_reduction"] / (df["budget"] + 1)
    df["category_enc"] = pd.factorize(df["category"])[0]
    df["region_enc"] = pd.factorize(df["region"])[0]
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data", "projects.csv"))
    ap.add_argument("--trials", type=int, default=50)
    ap.add_argument("--gdp", dest="gdp", action="store_true", default=True,
                    help="include country_gdp_per_capita as a feature (default)")
    ap.add_argument("--no-gdp", dest="gdp", action="store_false",
                    help="exclude GDP -> serving-compatible 11-feature model")
    ap.add_argument("--no-save", dest="save", action="store_false", default=True,
                    help="tune + report but do not overwrite the model files")
    args = ap.parse_args()

    data_path = args.data if os.path.isabs(args.data) or os.path.exists(args.data) \
        else os.path.join(ROOT, args.data)
    print(f"Training on: {data_path}")
    df = pd.read_csv(data_path)
    df = engineer(df)

    cols = list(BASE_COLS)
    use_gdp = args.gdp and "country_gdp_per_capita" in df.columns
    if use_gdp:
        df["country_gdp_per_capita"] = pd.to_numeric(df["country_gdp_per_capita"], errors="coerce")
        df["country_gdp_per_capita"] = df["country_gdp_per_capita"].fillna(df["country_gdp_per_capita"].median())
        cols.append("country_gdp_per_capita")
    print(f"Features ({len(cols)}): {cols}")

    X = df[cols].astype(float)
    y = df["success"].astype(int).values

    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    sc = StandardScaler()
    Xtr_s = pd.DataFrame(sc.fit_transform(Xtr), columns=cols)
    Xte_s = pd.DataFrame(sc.transform(Xte), columns=cols)

    cv = StratifiedKFold(n_splits=3, shuffle=True, random_state=42)

    def build(params):
        if _HAS_XGB:
            return XGBClassifier(
                tree_method="hist", eval_metric="logloss", n_jobs=-1,
                random_state=42, **params,
            )
        return GradientBoostingClassifier(random_state=42, **params)

    def objective(trial):
        if _HAS_XGB:
            params = {
                "n_estimators": trial.suggest_int("n_estimators", 100, 600),
                "max_depth": trial.suggest_int("max_depth", 3, 10),
                "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
                "subsample": trial.suggest_float("subsample", 0.6, 1.0),
                "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
                "min_child_weight": trial.suggest_int("min_child_weight", 1, 10),
                "reg_lambda": trial.suggest_float("reg_lambda", 1e-3, 10.0, log=True),
            }
        else:
            params = {
                "n_estimators": trial.suggest_int("n_estimators", 100, 400),
                "max_depth": trial.suggest_int("max_depth", 2, 6),
                "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
                "subsample": trial.suggest_float("subsample", 0.6, 1.0),
            }
        return cross_val_score(build(params), Xtr_s, ytr, cv=cv, scoring="roc_auc", n_jobs=-1).mean()

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
    print(f"Running Optuna: {args.trials} trials ({'XGBoost' if _HAS_XGB else 'GradientBoosting'})...")
    study.optimize(objective, n_trials=args.trials, show_progress_bar=False)
    print(f"Best CV AUC: {study.best_value:.4f}")
    print(f"Best params: {study.best_params}")

    best = build(study.best_params)
    best.fit(Xtr_s, ytr)
    test_auc = roc_auc_score(yte, best.predict_proba(Xte_s)[:, 1])
    cal = CalibratedClassifierCV(estimator=best, method="sigmoid", cv="prefit")
    cal.fit(Xte_s, yte)
    probs = cal.predict_proba(Xte_s)[:, 1]
    print(f"Holdout AUC: {test_auc:.4f}  Brier: {brier_score_loss(yte, probs):.4f}  mean_prob: {probs.mean():.3f}")

    if not args.save:
        print("(--no-save: model files not written)")
        return

    mdir = os.path.join(ROOT, "models")
    with open(os.path.join(mdir, "ensemble_model_v2.pkl"), "wb") as f:
        pickle.dump(best, f)
    with open(os.path.join(mdir, "ensemble_model_v2_cal.pkl"), "wb") as f:
        pickle.dump(cal, f)
    with open(os.path.join(mdir, "scaler_v2.pkl"), "wb") as f:
        pickle.dump(sc, f)
    cats = dict(zip(df["category"], df["category_enc"]))
    regs = dict(zip(df["region"], df["region_enc"]))
    with open(os.path.join(mdir, "cat_encodings.json"), "w") as f:
        json.dump({"category": cats, "region": regs}, f)
    with open(os.path.join(mdir, "ensemble_v2_optuna_meta.json"), "w") as f:
        json.dump({"features": cols, "use_gdp": use_gdp, "cv_auc": study.best_value,
                   "holdout_auc": test_auc, "best_params": study.best_params,
                   "n_rows": len(df), "n_trials": args.trials}, f, indent=2)
    print(f"Saved model + scaler + encodings ({len(cols)} features, use_gdp={use_gdp})")
    if use_gdp:
        print("WARNING: model uses country_gdp_per_capita — not servable by the current "
              "/predict path (no country input). See module docstring.")


if __name__ == "__main__":
    main()
