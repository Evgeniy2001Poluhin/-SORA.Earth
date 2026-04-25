import pickle, numpy as np
import pandas as pd

with open("models/ensemble_model_v2.pkl","rb") as f: ens = pickle.load(f)
with open("models/ensemble_model_v2_cal.pkl","rb") as f: cal = pickle.load(f)

print("=== Models ===")
print("ensemble:   ", type(ens).__name__, "| module:", type(ens).__module__)
print("calibrated: ", type(cal).__name__, "| module:", type(cal).__module__)

print()
print("=== Calibrator details ===")
if hasattr(cal, "calibrated_classifiers_"):
    print("n_calibrators:", len(cal.calibrated_classifiers_))
    for i, c in enumerate(cal.calibrated_classifiers_):
        method = getattr(c, "method", "?")
        base = getattr(c, "estimator", getattr(c, "base_estimator", None))
        print("  [", i, "] method:", method, "| base:", type(base).__name__)
        if hasattr(c, "calibrators_"):
            for j, k in enumerate(c.calibrators_):
                print("      calibrator[", j, "]:", type(k).__name__)
else:
    print("no calibrated_classifiers_ attribute")
    print("attrs:", [a for a in dir(cal) if not a.startswith("_")][:20])

print()
print("=== Probability comparison on 5 random points ===")
rng = np.random.RandomState(42)
COLS = ["budget","co2_reduction","social_impact","duration_months",
        "budget_per_month","co2_per_dollar","efficiency_score",
        "impact_ratio","budget_efficiency","category_enc","region_enc"]
X = pd.DataFrame(rng.uniform(0, 1, (5, 11)), columns=COLS)
print("idx  ensemble    calibrated   diff")
p_ens = ens.predict_proba(X)[:, 1]
p_cal = cal.predict_proba(X)[:, 1]
pairs = list(zip(p_ens, p_cal))
for i in range(len(pairs)):
    a, b = pairs[i]
    print(f"{i:>3}  {a:>8.4f}    {b:>8.4f}    {b-a:+.4f}")
