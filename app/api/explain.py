"""SHAP explainability endpoints."""
import os, pickle, numpy as np, shap
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from app.schemas import ProjectInput as Project
from app.validators import ProjectInput as LegacyProjectInput

router = APIRouter(tags=["explainability"])
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def _to_legacy(p):
    return LegacyProjectInput(
        budget=p.budget, co2_reduction=p.co2_reduction,
        social_impact=p.social_impact, duration_months=p.duration_months,
        category=getattr(p, "category", "Solar Energy"),
    )

@router.post("/predict/explain")
def explain_prediction(project: Project):
    import app.main as m
    feats = m.make_features(_to_legacy(project))
    names = list(feats.columns) if hasattr(feats, "columns") else [f"f{i}" for i in range(feats.shape[1])]

    explainer = shap.TreeExplainer(m.rf_model)
    raw_sv = explainer.shap_values(feats)

    if isinstance(raw_sv, list):
        sv = np.asarray(raw_sv[1][0], dtype=float)
    else:
        arr = np.asarray(raw_sv)
        if arr.ndim == 3:
            if arr.shape[-1] == 2:
                sv = np.asarray(arr[0, :, 1], dtype=float)
            else:
                sv = np.asarray(arr[0, :, 0], dtype=float)
        elif arr.ndim == 2:
            sv = np.asarray(arr[0], dtype=float)
        else:
            sv = np.asarray(arr, dtype=float).reshape(-1)

    base = explainer.expected_value
    if isinstance(base, (list, np.ndarray)):
        base_arr = np.asarray(base, dtype=float).reshape(-1)
        base = float(base_arr[1] if len(base_arr) > 1 else base_arr[0])
    else:
        base = float(base)

    proba = float(m.rf_model.predict_proba(feats)[0][1])
    vals = feats.values.flatten() if hasattr(feats, "values") else feats.flatten()

    contribs_raw = [
        {"feature": n, "value": round(float(v), 4),
         "shap_value": float(sv_i),
         "direction": "positive" if float(sv_i) > 0 else "negative"}
        for n, v, sv_i in zip(names, vals, sv)
    ]
    contribs_raw = sorted(contribs_raw, key=lambda x: abs(x["shap_value"]), reverse=True)

    abs_vals = [abs(c["shap_value"]) for c in contribs_raw]
    if abs_vals:
        max_abs = max(abs_vals)
        thresholds = {
            "high": max_abs * 0.7,
            "medium": max_abs * 0.3,
        }
    else:
        thresholds = {"high": 0.0, "medium": 0.0}

    contribs = []
    for c in contribs_raw:
        a = abs(c["shap_value"])
        if a >= thresholds["high"]:
            level = "high"
        elif a >= thresholds["medium"]:
            level = "medium"
        else:
            level = "low"
        contribs.append({
            "feature": c["feature"],
            "value": c["value"],
            "shap_value": round(c["shap_value"], 6),
            "direction": c["direction"],
            "impact": level,
        })

    return {
        "prediction": int(proba >= m.best_threshold),
        "probability": round(proba * 100, 2),
        "base_value": round(base, 6),
        "explanation": contribs,
        "all_features": [
            {
                "name": c["feature"],
                "direction": c["direction"],
                "impact": c["impact"],
                "shap_value": c["shap_value"],
                "value": c["value"],
            } for c in contribs
        ],
        "top_factors": [f"{c['feature']} ({c['impact']}: {c['shap_value']:+.4f})" for c in contribs[:3]],
        "top_features": [c["feature"] for c in contribs[:3]],
        "verdict": "success" if proba >= m.best_threshold else "low_probability",
    }


@router.post("/predict/explain/waterfall")
def explain_waterfall(project: Project):
    import app.main as m
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    feats = m.make_features(_to_legacy(project))
    names = list(feats.columns) if hasattr(feats, "columns") else [f"f{i}" for i in range(feats.shape[1])]

    explainer = shap.TreeExplainer(m.rf_model)
    explanation = explainer(feats)

    if hasattr(explanation, "values") and explanation.values.ndim == 3:
        explanation = shap.Explanation(
            values=explanation.values[:, :, 1],
            base_values=explanation.base_values[:, 1] if explanation.base_values.ndim > 1 else explanation.base_values,
            data=explanation.data, feature_names=names,
        )

    out = os.path.join(ROOT, "data", "shap_waterfall.png")
    shap.plots.waterfall(explanation[0], show=False)
    plt.tight_layout(); plt.savefig(out, dpi=150, bbox_inches="tight"); plt.close()
    return FileResponse(out, media_type="image/png", filename="shap_waterfall.png")


@router.get("/explain/beeswarm")
def beeswarm_plot():
    """Beeswarm plot from training data SHAP values."""
    import app.main as m
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    csv_path = os.path.join(ROOT, "data", "projects.csv")
    if not os.path.exists(csv_path):
        raise HTTPException(404, "Training data not found")

    df = pd.read_csv(csv_path)
    if len(df) > 200:
        df = df.sample(200, random_state=42)

    from app.validators import ProjectInput as PI
    feats_list = []
    for _, row in df.iterrows():
        try:
            p = PI(budget=row.get("budget", 10000), co2_reduction=row.get("co2_reduction", 50),
                   social_impact=row.get("social_impact", 50), duration_months=row.get("duration_months", 12))
            feats_list.append(m.make_features(p))
        except Exception:
            continue

    if not feats_list:
        raise HTTPException(500, "No valid samples")

    X = pd.concat(feats_list, ignore_index=True)
    explainer = shap.TreeExplainer(m.rf_model)
    explanation = explainer(X)

    if hasattr(explanation, "values") and explanation.values.ndim == 3:
        explanation = shap.Explanation(
            values=explanation.values[:, :, 1],
            base_values=explanation.base_values[:, 1] if explanation.base_values.ndim > 1 else explanation.base_values,
            data=explanation.data, feature_names=list(X.columns),
        )

    out = os.path.join(ROOT, "data", "shap_beeswarm.png")
    shap.plots.beeswarm(explanation, show=False, max_display=len(X.columns))
    plt.tight_layout(); plt.savefig(out, dpi=150, bbox_inches="tight"); plt.close()
    return FileResponse(out, media_type="image/png", filename="shap_beeswarm.png")
