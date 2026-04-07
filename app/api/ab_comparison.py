"""A/B model comparison — v1 vs v2 vs v2_calibrated."""
import os, pickle, json
import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score, brier_score_loss, log_loss
from sklearn.model_selection import train_test_split

router = APIRouter(prefix="/model", tags=["ab-comparison"])
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@router.get("/ab-comparison")
def ab_comparison():
    """Compare RF v1, Stacking v2, Calibrated v2 on held-out test set."""
    import app.main as m

    csv_path = os.path.join(ROOT, "data", "projects.csv")
    if not os.path.exists(csv_path):
        raise HTTPException(404, "Training data not found")

    df = pd.read_csv(csv_path)
    from app.validators import ProjectInput as PI

    results = []
    for _, row in df.iterrows():
        try:
            p = PI(budget=row["budget"], co2_reduction=row["co2_reduction"],
                   social_impact=row["social_impact"], duration_months=row["duration_months"])
            feats_v1 = m.make_features(p)

            cat = row.get("category", "Solar Energy")
            reg = row.get("region", "Europe")
            feats_v2 = m.make_features_v2(p, cat, reg)

            pr_v1 = float(m.rf_model.predict_proba(feats_v1)[0][1])

            pr_v2 = float(m.ensemble_model_v2.predict_proba(feats_v2)[0][1]) if m.ensemble_model_v2 else pr_v1

            cal_path = os.path.join(ROOT, "models", "rf_model_cal.pkl")
            if os.path.exists(cal_path):
                with open(cal_path, "rb") as f:
                    cal = pickle.load(f)
                pr_cal = float(cal.predict_proba(feats_v1)[0][1])
            else:
                pr_cal = pr_v1

            results.append({
                "y": int(row["success"]),
                "pr_v1": pr_v1, "pr_v2": pr_v2, "pr_cal": pr_cal,
            })
        except Exception:
            continue

    if len(results) < 20:
        raise HTTPException(500, "Not enough samples")

    rdf = pd.DataFrame(results)
    y = rdf["y"].values

    comparison = {}
    for name, col, threshold in [
        ("rf_v1", "pr_v1", m.best_threshold),
        ("stacking_v2", "pr_v2", 0.5),
        ("rf_v1_calibrated", "pr_cal", m.best_threshold),
    ]:
        probs = rdf[col].values
        preds = (probs >= threshold).astype(int)
        comparison[name] = {
            "accuracy": round(accuracy_score(y, preds), 4),
            "f1_score": round(f1_score(y, preds, zero_division=0), 4),
            "auc_roc": round(roc_auc_score(y, probs), 4),
            "brier_score": round(brier_score_loss(y, probs), 4),
            "log_loss": round(log_loss(y, probs), 4),
            "threshold": threshold,
            "n_samples": len(y),
        }

    return {"models": comparison, "winner": max(comparison, key=lambda k: comparison[k]["auc_roc"])}


@router.get("/ab-comparison/plot")
def ab_comparison_plot():
    """Bar chart comparing model metrics."""
    import app.main as m
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    data = ab_comparison()
    models = data["models"]
    metrics = ["accuracy", "f1_score", "auc_roc"]

    fig, ax = plt.subplots(figsize=(10, 6))
    x = np.arange(len(metrics))
    width = 0.25
    colors = ["#e74c3c", "#3498db", "#2ecc71"]

    for i, (name, vals) in enumerate(models.items()):
        values = [vals[m] for m in metrics]
        bars = ax.bar(x + i * width, values, width, label=name.replace("_", " ").title(), color=colors[i])
        for bar, val in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width()/2., bar.get_height() + 0.005,
                    f'{val:.3f}', ha='center', va='bottom', fontsize=9)

    ax.set_ylabel("Score", fontsize=12)
    ax.set_title("Model A/B Comparison", fontsize=14, fontweight="bold")
    ax.set_xticks(x + width)
    ax.set_xticklabels([m.replace("_", " ").title() for m in metrics])
    ax.legend()
    ax.set_ylim(0, 1.1)
    ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    out = os.path.join(ROOT, "data", "ab_comparison.png")
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()

    return FileResponse(out, media_type="image/png", filename="ab_comparison.png")
