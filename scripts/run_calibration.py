import argparse, pickle, pandas as pd, numpy as np
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.metrics import brier_score_loss
import os

parser = argparse.ArgumentParser()
parser.add_argument("--model",  default="models/ensemble_model_v2.pkl")
parser.add_argument("--data",   default="data/calibration_set.csv")
parser.add_argument("--method", default="isotonic", choices=["isotonic","sigmoid"])
parser.add_argument("--features", nargs="+",
    default=["budget","co2_reduction","social_impact","duration_months"])
args = parser.parse_args()

with open(args.model, "rb") as f:
    base_model = pickle.load(f)

df = pd.read_csv(args.data)
X  = df[args.features].values
y  = df["approved"].values

probs_before = base_model.predict_proba(X)[:, 1]
print(f"Brier BEFORE: {brier_score_loss(y, probs_before):.4f}  mean_prob: {probs_before.mean():.3f}")

cal_model = CalibratedClassifierCV(estimator=base_model, method=args.method, cv="prefit")
cal_model.fit(X, y)

probs_after = cal_model.predict_proba(X)[:, 1]
print(f"Brier AFTER:  {brier_score_loss(y, probs_after):.4f}  mean_prob: {probs_after.mean():.3f}")

out = os.path.join(os.path.dirname(args.model), "ensemble_model_v2_cal.pkl")
with open(out, "wb") as f:
    pickle.dump(cal_model, f)
print(f"Saved: {out}")
