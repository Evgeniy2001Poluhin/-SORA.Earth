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
