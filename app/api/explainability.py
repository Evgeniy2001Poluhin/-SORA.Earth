from fastapi import APIRouter, HTTPException, Query
from typing import Dict
import numpy as np
import pandas as pd
import shap

router = APIRouter()

_explainer = None
_explainer_kind = None
_bg_df = None

FEATURE_COLS = [
    "budget", "co2_reduction", "social_impact", "duration_months",
    "budget_per_month", "co2_per_dollar", "efficiency_score",
    "impact_ratio", "budget_efficiency", "category_enc", "region_enc",
    "country_gdp_per_capita",
]

BASE_FEATURES = ["budget", "co2_reduction", "social_impact", "duration_months"]


def _resolve_state():
    from app.main import ensemble_model_v2, scaler_v2
    if ensemble_model_v2 is None or scaler_v2 is None:
        raise HTTPException(503, "ensemble_model_v2 / scaler not loaded")
    return ensemble_model_v2, scaler_v2


def _make_background(scaler, n=50):
    """The KernelExplainer's reference distribution, taken from the scaler.

    Every attribution `/explain/local` returns is measured *relative to this*.
    It used to be twelve hardcoded means and twelve hardcoded standard
    deviations, and measured against `models/scaler_v2.pkl` on 2026-09-21 the
    centre sat +99.45 sigma out on `budget_efficiency` and -7.02 on
    `social_impact`, with the spread wrong on nine of the twelve features --
    `budget` was 42,000 times too narrow, `budget_efficiency` fifty times too
    wide. Attributions against that reference describe a distribution the model
    never saw.

    A `StandardScaler` carries the training distribution exactly: `mean_` and
    `scale_` per feature, in `feature_names_in_` order, which
    `tests/test_the_shap_background_is_the_training_distribution.py` pins
    against `FEATURE_COLS`. There is nothing to guess.

    Still an approximation, and worth saying so: a Gaussian is a poor model of
    a distribution this skewed -- `budget` has a training mean of 9.9e7 against
    a spread of 7.9e8 -- so some draws are physically impossible, a negative
    budget among them. A sample of real rows would be better, and needs a
    decision about what serving code may read. What this fixes is the reference
    being in the wrong place entirely.
    """
    rng = np.random.RandomState(42)
    means = np.asarray(scaler.mean_, dtype=float)
    stds = np.asarray(scaler.scale_, dtype=float)
    raw = rng.normal(loc=means, scale=stds, size=(n, len(FEATURE_COLS)))
    df = pd.DataFrame(raw, columns=FEATURE_COLS)
    return scaler.transform(df), df


def _get_explainer():
    global _explainer, _explainer_kind, _bg_df
    if _explainer is not None:
        return _explainer, _explainer_kind, _bg_df
    model, scaler = _resolve_state()
    bg_scaled, bg_df = _make_background(scaler, n=50)
    _bg_df = bg_df
    _explainer = shap.KernelExplainer(model.predict_proba, bg_scaled)
    _explainer_kind = "kernel"
    return _explainer, _explainer_kind, _bg_df


@router.get("/explain/global", tags=["explainability"])
def explain_global(top_n: int = Query(10, ge=1, le=11), nsamples: int = 30):
    expl, kind, bg_df = _get_explainer()
    model, scaler = _resolve_state()
    sample_df = bg_df.head(20)
    sample_scaled = scaler.transform(sample_df)
    sv = expl.shap_values(sample_scaled, nsamples=nsamples, silent=True)
    sv_arr = np.asarray(sv)
    if isinstance(sv, list):
        sv_arr = np.asarray(sv[1])
    elif sv_arr.ndim == 3:
        sv_arr = sv_arr[:, :, 1]
    importance = np.abs(sv_arr).mean(axis=0)
    if importance.ndim > 1:
        importance = importance.mean(axis=-1)
    order = np.argsort(importance)[::-1][:top_n]
    return {
        "explainer": kind,
        "samples": int(sample_scaled.shape[0]),
        "nsamples_per_row": nsamples,
        "top_features": [
            {"feature": FEATURE_COLS[i], "importance": float(importance[i])}
            for i in order
        ],
    }


@router.post("/explain/local", tags=["explainability"])
def explain_local(features: Dict[str, float], top_n: int = 10, nsamples: int = 100):
    # Check for missing base features
    missing = [f for f in BASE_FEATURES if f not in features]
    if missing:
        raise HTTPException(422, f"Missing required features: {missing}")

    # Validate social_impact is on API scale (0-10)
    si = features.get("social_impact", 0.0)
    if si < 0 or si > 10:
        raise HTTPException(422, f"social_impact must be 0-10, got {si}")

    # Identify extra keys (not base features)
    ignored = sorted([k for k in features.keys() if k not in BASE_FEATURES])

    # Build ProjectInput from the four base features
    from app.validators import ProjectInput
    pi = ProjectInput(
        budget=features["budget"],
        co2_reduction=features["co2_reduction"],
        social_impact=features["social_impact"],
        duration_months=features["duration_months"],
    )

    expl, kind, _ = _get_explainer()
    model, scaler = _resolve_state()

    # Get raw features using the serving builder with serving defaults
    from app.main import make_features_v2_raw
    df_raw = make_features_v2_raw(pi, category="Solar Energy", region="Europe")

    # Scale the raw features
    Xs = scaler.transform(df_raw)

    # Compute SHAP on scaled input
    sv = expl.shap_values(Xs, nsamples=nsamples, silent=True)
    sv_arr = np.asarray(sv)
    if isinstance(sv, list):
        sv_arr = np.asarray(sv[1])
    elif sv_arr.ndim == 3:
        sv_arr = sv_arr[:, :, 1]  # take class-1 contributions
    contrib = sv_arr[0]
    if contrib.ndim > 1:
        contrib = contrib[:, -1]
    base = expl.expected_value
    if hasattr(base, "__len__"):
        base = float(base[1])

    # Compute prediction on SCALED input (same as serving)
    df_scaled = pd.DataFrame(Xs, columns=FEATURE_COLS)
    pred_proba = float(model.predict_proba(df_scaled)[0][1])

    order = np.argsort(np.abs(contrib))[::-1][:top_n]
    return {
        "explainer": kind,
        "base_value": float(base),
        "prediction_proba": pred_proba,
        "prediction": pred_proba,
        "ignored_features": ignored,
        "top_contributions": [
            {
                "feature": FEATURE_COLS[i],
                "scaled_value": float(Xs[0, i]),
                "raw_value": float(df_raw.iloc[0, i]),
                "shap": float(contrib[i]),
                "shap_value": float(contrib[i]),
                "direction": "up" if contrib[i] > 0 else "down",
            }
            for i in order
        ],
    }
