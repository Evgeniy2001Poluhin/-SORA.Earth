"""A/B model comparison — v1 vs v2 vs v2_calibrated."""
import io

from fastapi import Response
import os, pickle, json
import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score, brier_score_loss, log_loss
from sklearn.model_selection import train_test_split

from app.paths import data_dir, models_dir

router = APIRouter(prefix="/model", tags=["ab-comparison"])

# Maximum rows to evaluate (performance bound: each endpoint <15s)
MAX_AB_COMPARISON_ROWS = 200


@router.get("/ab-comparison")
def ab_comparison():
    """Compare RF v1, Stacking v2, Calibrated v2 on a deterministic sample of the training data (not held out)."""
    import app.main as m

    csv_path = os.path.join(data_dir(), "projects.csv")
    if not os.path.exists(csv_path):
        raise HTTPException(404, "Training data not found")

    df = pd.read_csv(csv_path)
    # Sample at most MAX_AB_COMPARISON_ROWS for performance
    if len(df) > MAX_AB_COMPARISON_ROWS:
        df = df.sample(MAX_AB_COMPARISON_ROWS, random_state=42)

    from app.validators import ProjectInput as PI
    from app.scales import social_impact_from_training

    # Build features per row (keep try/except semantics), collect for batching
    feats_v1_list, feats_v2_list, labels = [], [], []
    for _, row in df.iterrows():
        try:
            # Training rows are on model scale (0-100); convert to API scale (0-10) for validator
            p = PI(budget=row["budget"], co2_reduction=row["co2_reduction"],
                   social_impact=social_impact_from_training(row["social_impact"]), duration_months=row["duration_months"])
            feats_v1 = m.make_features(p)

            cat = row.get("category", "Solar Energy")
            reg = row.get("region", "Europe")
            feats_v2 = m.make_features_v2(p, cat, reg)

            feats_v1_list.append(feats_v1)
            feats_v2_list.append(feats_v2)
            labels.append(int(row["success"]))
        except Exception:
            continue

    if len(labels) < 20:
        raise HTTPException(500, "Not enough samples")

    # Batch predictions: concat frames, call each model once
    X_v1 = pd.concat(feats_v1_list, ignore_index=True)
    X_v2 = pd.concat(feats_v2_list, ignore_index=True)
    y = np.array(labels)

    # RF v1: batch predict
    probs_v1 = m.rf_model.predict_proba(X_v1)[:, 1]

    # Ensemble v2: batch predict
    probs_v2 = m.ensemble_model_v2.predict_proba(X_v2)[:, 1] if m.ensemble_model_v2 else probs_v1

    # Calibrated RF: batch predict
    cal_path = os.path.join(models_dir(), "rf_model_cal.pkl")
    if os.path.exists(cal_path):
        with open(cal_path, "rb") as f:
            cal = pickle.load(f)
        probs_cal = cal.predict_proba(X_v1)[:, 1]
    else:
        probs_cal = probs_v1

    # Build results DataFrame
    rdf = pd.DataFrame({
        "y": y,
        "pr_v1": probs_v1,
        "pr_v2": probs_v2,
        "pr_cal": probs_cal,
    })

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
    # Rendered into memory: a GET does not rewrite a tracked file in data/.
    png = io.BytesIO()
    plt.savefig(png, format="png", dpi=150, bbox_inches="tight")
    plt.close()

    return Response(png.getvalue(), media_type="image/png",
                    headers={"Content-Disposition": 'attachment; filename="ab_comparison.png"'})
