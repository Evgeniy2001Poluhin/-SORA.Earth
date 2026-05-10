"""Evaluate @champion on held-out split and dump reliability JSON."""
import json, os, sys, datetime
import pandas as pd
from sklearn.model_selection import train_test_split
from types import SimpleNamespace
import mlflow, mlflow.sklearn
from mlflow.tracking import MlflowClient

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from app import main as m                                 # noqa: E402
from app.calibration_metrics import (                      # noqa: E402
    reliability_curve, expected_calibration_error, murphy_decomposition,
)

MODEL_NAME = os.getenv("ESG_MODEL_NAME", "esg-success-predictor")
ALIAS      = os.getenv("ESG_MODEL_ALIAS", "champion")
DATA       = os.getenv("ESG_CAL_DATA", os.path.join(ROOT, "data", "projects.csv"))
N_BINS     = 10
OUT        = os.path.join(ROOT, "output", "calibration_champion.json")

mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5556"))
model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}@{ALIAS}")
mv = MlflowClient().get_model_version_by_alias(MODEL_NAME, ALIAS)

df = pd.read_csv(DATA)
y_all = df["success"].astype(int).values

_, test_idx = train_test_split(
    range(len(df)), test_size=0.2, stratify=y_all, random_state=42,
)
probs, labels = [], []
for i in test_idx:
    row = df.iloc[i]
    try:
        p = SimpleNamespace(budget=float(row['budget']),co2_reduction=float(row['co2_reduction']),social_impact=float(row['social_impact']),duration_months=float(row['duration_months']))
        feats = m.make_features_v2(p, row.get("category", "Solar Energy"),
                                   row.get("region", "Europe"))
        pr = float(model.predict_proba(feats)[0][1])
        probs.append(pr); labels.append(int(row["success"]))
    except Exception as ex:
        print("skip", i, ex)

if len(probs) < 20:
    raise SystemExit(f"not enough valid samples: {len(probs)}")

curve  = reliability_curve(probs, labels, n_bins=N_BINS)
ece    = expected_calibration_error(probs, labels, n_bins=N_BINS)
murphy = murphy_decomposition(probs, labels, n_bins=N_BINS)

report = {
    "alias": ALIAS, "version": mv.version,
    "timestamp": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
    "n_samples": murphy["n_samples"], "n_bins": N_BINS,
    "base_rate": murphy["base_rate"],
    "brier": murphy["brier"], "ece": ece,
    "curve": curve,
    "murphy": {
        "reliability": murphy["reliability"],
        "resolution":  murphy["resolution"],
        "uncertainty": murphy["uncertainty"],
    },
}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w") as f:
    json.dump(report, f, indent=2)
print(f"saved {OUT}  v{mv.version}@{ALIAS}  n={report['n_samples']}  "
      f"brier={report['brier']:.4f}  ece={ece:.4f}")

