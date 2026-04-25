"""Calibration & uncertainty endpoints for thesis."""
import os, pickle, json
import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(tags=["calibration"])
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@router.get("/model/reliability-diagram")
def reliability_diagram():
    import app.main as m
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from sklearn.calibration import calibration_curve

    csv_path = os.path.join(ROOT, "data", "projects.csv")
    if not os.path.exists(csv_path):
        raise HTTPException(404, "Training data not found")

    df = pd.read_csv(csv_path)
    from app.validators import ProjectInput as PI

    probas_v1, probas_v2, probas_cal, labels = [], [], [], []
    # Pre-load calibrated model once
    cal_path = os.path.join(ROOT, "models", "ensemble_model_v2_cal.pkl")
    cal_model = None
    if os.path.exists(cal_path) and m.ensemble_model_v2:
        with open(cal_path, "rb") as f:
            cal_model = pickle.load(f)
    for _, row in df.iterrows():
        try:
            p = PI(budget=row.get("budget", 10000), co2_reduction=row.get("co2_reduction", 50),
                   social_impact=row.get("social_impact", 5), duration_months=row.get("duration_months", 12))
            feats = m.make_features(p)
            pr1 = float(m.rf_model.predict_proba(feats)[0][1])
            probas_v1.append(pr1)

            cat = row.get("category", "Solar Energy") if "category" in row else "Solar Energy"
            reg = row.get("region", "Europe") if "region" in row else "Europe"
            if m.ensemble_model_v2:
                feats2 = m.make_features_v2(p, cat, reg)
                pr2 = float(m.ensemble_model_v2.predict_proba(feats2)[0][1])
                probas_v2.append(pr2)
            else:
                probas_v2.append(pr1)

            if cal_model is not None:
                pr_cal = float(cal_model.predict_proba(feats2)[0][1])
                probas_cal.append(pr_cal)
            else:
                probas_cal.append(pr2)

            labels.append(int(row.get("success", row.get("is_successful", 0))))
        except Exception:
            continue

    if len(labels) < 20:
        raise HTTPException(500, "Not enough valid samples")

    y = np.array(labels)
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    ax = axes[0]
    for name, probs, color, ls in [
        ("RF v1", probas_v1, "#e74c3c", "--"),
        ("Stacking v2", probas_v2, "#3498db", "-"),
        ("Calibrated v2", probas_cal, "#2ecc71", "-"),
    ]:
        prob_true, prob_pred = calibration_curve(y, probs, n_bins=8, strategy="uniform")
        ax.plot(prob_pred, prob_true, marker="o", label=name, color=color, linestyle=ls, linewidth=2)

    ax.plot([0, 1], [0, 1], "k--", alpha=0.5, label="Perfect calibration")
    ax.set_xlabel("Mean predicted probability", fontsize=12)
    ax.set_ylabel("Fraction of positives", fontsize=12)
    ax.set_title("Reliability Diagram", fontsize=14, fontweight="bold")
    ax.legend(loc="lower right")
    ax.grid(True, alpha=0.3)

    ax2 = axes[1]
    ax2.hist(probas_v1, bins=20, alpha=0.4, label="RF v1", color="#e74c3c")
    ax2.hist(probas_v2, bins=20, alpha=0.4, label="Stacking v2", color="#3498db")
    ax2.hist(probas_cal, bins=20, alpha=0.4, label="Calibrated v2", color="#2ecc71")
    ax2.set_xlabel("Predicted probability", fontsize=12)
    ax2.set_ylabel("Count", fontsize=12)
    ax2.set_title("Prediction Distribution", fontsize=14, fontweight="bold")
    ax2.legend()
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    out = os.path.join(ROOT, "data", "reliability_diagram.png")
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()

    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
    metrics = {}
    for name, probs in [("rf_v1", probas_v1), ("stacking_v2", probas_v2), ("calibrated_v2", probas_cal)]:
        metrics[name] = {
            "brier_score": round(brier_score_loss(y, probs), 4),
            "log_loss": round(log_loss(y, probs), 4),
            "auc_roc": round(roc_auc_score(y, probs), 4),
        }

    return FileResponse(out, media_type="image/png", filename="reliability_diagram.png",
                        headers={"X-Metrics": json.dumps(metrics), "X-Samples": str(len(labels))})


@router.post("/predict/uncertainty")
def predict_with_uncertainty(project: dict):
    import app.main as m
    from app.schemas import ProjectInput as Project
    from app.validators import ProjectInput as PI

    p = Project(**project)
    legacy = PI(budget=p.budget, co2_reduction=p.co2_reduction,
                social_impact=p.social_impact, duration_months=p.duration_months)

    feats = m.make_features(legacy)
    base_proba = float(m.rf_model.predict_proba(feats)[0][1])

    tree_preds = np.array([t.predict_proba(feats.values if hasattr(feats, "values") else feats)[0][1]
                           for t in m.rf_model.estimators_])

    return {
        "probability": round(base_proba * 100, 2),
        "uncertainty": {
            "method": "RF tree variance",
            "mean": round(float(np.mean(tree_preds)) * 100, 2),
            "std": round(float(np.std(tree_preds)) * 100, 2),
            "ci_90": [round(float(np.percentile(tree_preds, 5)) * 100, 2),
                      round(float(np.percentile(tree_preds, 95)) * 100, 2)],
            "n_trees": len(m.rf_model.estimators_),
        },
        "reliability": "high" if np.std(tree_preds) < 0.1 else "medium" if np.std(tree_preds) < 0.2 else "low",
    }


@router.post("/calibration/discrepancy")
def calibration_discrepancy(project: dict):
    """Cross-model divergence: rf_v1 vs stacking_v2 vs calibrated_v2.

    Returns per-model proba + consensus + spread/std + RF tree uncertainty.
    Useful for surfacing model disagreement to end-users (and thesis ch. 5).
    """
    import app.main as m
    from app.validators import ProjectInput as PI

    p_in = PI(
        budget=float(project.get("budget", 10000)),
        co2_reduction=float(project.get("co2_reduction", 50)),
        social_impact=float(project.get("social_impact", 5)),
        duration_months=float(project.get("duration_months", 12)),
    )
    cat = project.get("category", "Solar Energy")
    reg = project.get("region", "Europe")

    out = {"models": {}, "consensus": {}, "divergence": {}, "tree_uncertainty": {}}

    # rf_v1
    feats = m.make_features(p_in)
    pr1 = float(m.rf_model.predict_proba(feats)[0][1])
    out["models"]["rf_v1"] = {"proba": round(pr1, 6), "weight": 0.2}

    # stacking_v2 (uncalibrated): unwrap base estimator if model is CalibratedClassifierCV
    pr2 = pr1
    feats2 = None
    if m.ensemble_model_v2 is not None:
        feats2 = m.make_features_v2(p_in, cat, reg)
        base_est = m.ensemble_model_v2
        if hasattr(base_est, "calibrated_classifiers_"):
            inner = base_est.calibrated_classifiers_[0]
            base_est = getattr(inner, "estimator", getattr(inner, "base_estimator", base_est))
        pr2 = float(base_est.predict_proba(feats2)[0][1])
    out["models"]["stacking_v2"] = {"proba": round(pr2, 6), "weight": 0.4}

    # calibrated_v2
    pr_cal = pr2
    cal_path = os.path.join(ROOT, "models", "ensemble_model_v2_cal.pkl")
    if os.path.exists(cal_path) and m.ensemble_model_v2 is not None:
        with open(cal_path, "rb") as f:
            cal_model = pickle.load(f)
        feats2 = m.make_features_v2(p_in, cat, reg)
        pr_cal = float(cal_model.predict_proba(feats2)[0][1])
    out["models"]["calibrated_v2"] = {"proba": round(pr_cal, 6), "weight": 0.4}

    # consensus (weighted)
    probs = [out["models"][k]["proba"] for k in ("rf_v1", "stacking_v2", "calibrated_v2")]
    weights = [out["models"][k]["weight"] for k in ("rf_v1", "stacking_v2", "calibrated_v2")]
    weighted = sum(p * w for p, w in zip(probs, weights)) / sum(weights)
    out["consensus"] = {"weighted_proba": round(weighted, 4), "method": "weighted_avg"}

    # divergence
    pmax = max(probs); pmin = min(probs)
    spread = pmax - pmin
    names = list(out["models"].keys())
    pair = (names[probs.index(pmax)], names[probs.index(pmin)])
    out["divergence"] = {
        "max_spread": round(spread, 4),
        "std": round(float(np.std(probs)), 4),
        "max_pair": list(pair),
    }
    if spread > 0.15:
        out["recommendation"] = "high_disagreement"
    elif spread > 0.07:
        out["recommendation"] = "moderate_disagreement"
    else:
        out["recommendation"] = "consensus"

    # RF tree uncertainty (bonus)
    try:
        tree_preds = np.array([
            t.predict_proba(feats.values if hasattr(feats, "values") else feats)[0][1]
            for t in m.rf_model.estimators_
        ])
        out["tree_uncertainty"] = {
            "std": round(float(np.std(tree_preds)), 4),
            "ci_90": [round(float(np.percentile(tree_preds, 5)), 4),
                      round(float(np.percentile(tree_preds, 95)), 4)],
            "n_trees": int(len(m.rf_model.estimators_)),
        }
    except Exception:
        pass

    return out

