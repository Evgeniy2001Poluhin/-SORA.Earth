import os, pickle
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data", "projects.csv")
MODELS = os.path.join(BASE, "models")

df = pd.read_csv(DATA).dropna(subset=["success"]).reset_index(drop=True)
df["success"] = df["success"].astype(int)

df["budget_per_month"]  = df["budget"] / df["duration_months"].clip(lower=1)
df["co2_per_dollar"]    = df["co2_reduction"] / df["budget"].clip(lower=1) * 1000
df["efficiency_score"]  = (df["co2_reduction"] * df["social_impact"]) / df["duration_months"].clip(lower=1)
df["impact_ratio"]      = df["social_impact"] / df["co2_reduction"].clip(lower=1)
df["budget_efficiency"] = df["co2_reduction"] / df["budget_per_month"].clip(lower=1)

for col in ["category", "region"]:
    mapping = df.groupby(col)["success"].mean()
    df[col + "_enc"] = df[col].map(mapping)

FEATURES = ["budget","co2_reduction","social_impact","duration_months",
            "budget_per_month","co2_per_dollar","efficiency_score",
            "impact_ratio","budget_efficiency","category_enc","region_enc"]
X, y = df[FEATURES], df["success"]

with open(os.path.join(MODELS, "scaler_v2.pkl"), "rb") as f:
    scaler = pickle.load(f)
with open(os.path.join(MODELS, "ensemble_model_v2.pkl"), "rb") as f:
    base = pickle.load(f)

X_scaled = pd.DataFrame(scaler.transform(X), columns=FEATURES)
cal = CalibratedClassifierCV(estimator=base, cv="prefit", method="isotonic")
cal.fit(X_scaled, y)

out = os.path.join(MODELS, "ensemble_model_v2_cal.pkl")
with open(out, "wb") as f:
    pickle.dump(cal, f)
print("WROTE", out, "->", type(cal).__name__)
