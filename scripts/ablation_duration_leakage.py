#!/usr/bin/env python3
"""Controlled offline ablation of the duration_months leakage (#232).

Answers one question with numbers: how much of the serving model's headline
AUC comes from `duration_months` (and features derived from it), which
`docs/DATASET_AUDIT_2026-09-04_LIVE_FETCH.md` showed is a near-deterministic
function of the same fact `success` is built from.

It trains the *serving* model class (RandomForest, matching
`models/model.pkl`) on the same file it was trained on -- the `worldbank` rows
of `data/projects.csv` -- and compares five feature sets on held-out folds:

  A current          the 9 columns make_features() builds
  B no_duration      A minus duration_months (budget_per_month kept)
  C no_date_derived  A minus duration_months AND budget_per_month
  D leakage_only     duration_months + budget_per_month only
  E permissible_esg  budget, social_impact, co2_reduction, category, region,
                     country_gdp_per_capita -- nothing derived from closing date

Metrics per set: ROC-AUC, PR-AUC (average precision), Brier score, all on
out-of-fold predictions from StratifiedKFold, with 1000-sample bootstrap 95%
CIs over the pooled OOF predictions.

Split caveat, stated plainly: `data/projects.csv` carries neither
`source_project_id` nor any date, so a grouped/temporal split (#232 item 3)
cannot be run on it -- that needs the live-fetch dataset. Exact-duplicate rows
are dropped here; the remaining budget/duration collisions between distinct
projects, if anything, inflate every AUC below, so a clean set that still
collapses is a lower bound on the leakage, not an artefact of the split.

Reproduce:
    python3 scripts/ablation_duration_leakage.py --data data/projects.csv
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd


RANDOM_STATE = 20260916
N_SPLITS = 5
N_BOOTSTRAP = 1000


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """The 9 make_features() columns plus the raw ESG inputs, from the CSV.

    Derived columns use the exact formulas in app/main.py::make_features so the
    "current" set matches what the serving model sees. year/quarter are the
    retrain-clock constants (constant across rows, as in production).
    """
    out = pd.DataFrame(index=df.index)
    out["budget"] = df["budget"].astype(float)
    out["co2_reduction"] = df["co2_reduction"].astype(float)
    out["social_impact"] = df["social_impact"].astype(float)
    out["duration_months"] = df["duration_months"].astype(float)
    dur = out["duration_months"].clip(lower=1)
    out["budget_per_month"] = out["budget"] / dur
    out["co2_per_dollar"] = out["co2_reduction"] / out["budget"].clip(lower=1) * 1000
    out["efficiency_score"] = (out["co2_reduction"] * out["social_impact"]) / dur
    out["year"] = 2026.0
    out["quarter"] = 3.0
    # Raw ESG inputs not in the model, for the permissible-only set.
    out["country_gdp_per_capita"] = pd.to_numeric(
        df.get("country_gdp_per_capita", 0), errors="coerce"
    ).fillna(0.0)
    for cat in ("category", "region"):
        col = df.get(cat)
        out[cat] = col.astype(str) if col is not None else "NA"
    return out


FEATURE_SETS = {
    "A_current": [
        "budget", "co2_reduction", "social_impact", "duration_months",
        "budget_per_month", "co2_per_dollar", "efficiency_score", "year", "quarter",
    ],
    "B_no_duration": [
        "budget", "co2_reduction", "social_impact",
        "budget_per_month", "co2_per_dollar", "efficiency_score", "year", "quarter",
    ],
    "C_no_date_derived": [
        "budget", "co2_reduction", "social_impact",
        "co2_per_dollar", "efficiency_score", "year", "quarter",
    ],
    "D_leakage_only": ["duration_months", "budget_per_month"],
    "E_permissible_esg": [
        "budget", "co2_reduction", "social_impact",
        "country_gdp_per_capita", "category", "region",
    ],
}


def _encode(X: pd.DataFrame) -> pd.DataFrame:
    """One-hot the two categoricals if present; leave numerics alone."""
    cat_cols = [c for c in ("category", "region") if c in X.columns]
    if cat_cols:
        X = pd.get_dummies(X, columns=cat_cols, dummy_na=False)
    return X.astype(float)


def _bootstrap_ci(y_true, y_prob, metric, n=N_BOOTSTRAP, seed=RANDOM_STATE):
    rng = np.random.default_rng(seed)
    n_rows = len(y_true)
    vals = []
    yt = np.asarray(y_true)
    yp = np.asarray(y_prob)
    for _ in range(n):
        idx = rng.integers(0, n_rows, n_rows)
        if len(np.unique(yt[idx])) < 2:
            continue
        vals.append(metric(yt[idx], yp[idx]))
    if not vals:
        return (float("nan"), float("nan"))
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5)))


def evaluate(name, X_full, y):
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
    from sklearn.model_selection import StratifiedKFold

    cols = FEATURE_SETS[name]
    X = _encode(X_full[cols].copy())
    skf = StratifiedKFold(n_splits=N_SPLITS, shuffle=True, random_state=RANDOM_STATE)
    oof = np.zeros(len(y), dtype=float)
    for tr, te in skf.split(X, y):
        clf = RandomForestClassifier(
            n_estimators=200, random_state=RANDOM_STATE, n_jobs=-1
        )
        clf.fit(X.iloc[tr], y.iloc[tr])
        oof[te] = clf.predict_proba(X.iloc[te])[:, 1]

    roc = roc_auc_score(y, oof)
    pr = average_precision_score(y, oof)
    brier = brier_score_loss(y, oof)
    roc_ci = _bootstrap_ci(y, oof, roc_auc_score)
    pr_ci = _bootstrap_ci(y, oof, average_precision_score)
    return {
        "features": cols,
        "n_features_encoded": X.shape[1],
        "roc_auc": round(roc, 4),
        "roc_auc_ci95": [round(roc_ci[0], 4), round(roc_ci[1], 4)],
        "pr_auc": round(pr, 4),
        "pr_auc_ci95": [round(pr_ci[0], 4), round(pr_ci[1], 4)],
        "brier": round(brier, 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/projects.csv")
    ap.add_argument("--source", default="worldbank")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    df = pd.read_csv(args.data)
    if "source" in df.columns and args.source:
        df = df[df["source"] == args.source].copy()
    before = len(df)
    df = df.drop_duplicates().reset_index(drop=True)
    df = df.dropna(subset=["budget", "duration_months", "success"]).reset_index(drop=True)
    df["success"] = df["success"].astype(int)

    y = df["success"]
    X_full = build_features(df)

    print(f"rows: {before} -> {len(df)} after dropping exact duplicates/NaN")
    print(f"class balance: success=1 {int(y.sum())} ({y.mean():.1%}), "
          f"success=0 {int((1 - y).sum())} ({1 - y.mean():.1%})")
    print("naive budget-synthetic-duration rule accuracy: see "
          "scripts/check_duration_success_leakage.py (0.8547 on this file)")
    print()

    results = {}
    header = f"{'set':<20} {'ROC-AUC (95% CI)':<26} {'PR-AUC (95% CI)':<26} {'Brier':<8}"
    print(header)
    print("-" * len(header))
    for name in FEATURE_SETS:
        r = evaluate(name, X_full, y)
        results[name] = r
        roc = f"{r['roc_auc']:.3f} [{r['roc_auc_ci95'][0]:.3f},{r['roc_auc_ci95'][1]:.3f}]"
        pr = f"{r['pr_auc']:.3f} [{r['pr_auc_ci95'][0]:.3f},{r['pr_auc_ci95'][1]:.3f}]"
        print(f"{name:<20} {roc:<26} {pr:<26} {r['brier']:<8}")

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(
                {
                    "n_rows": len(df),
                    "class_balance_pos": float(y.mean()),
                    "results": results,
                },
                fh,
                indent=2,
            )
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
