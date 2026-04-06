import json, numpy as np, joblib, pandas as pd
from sklearn.metrics import accuracy_score, roc_auc_score, f1_score
from pathlib import Path
import shap

REPORTS_DIR = Path("reports")
DATA_PATH   = Path("data/projects.csv")

for model_name in ["models/ensemble_model.pkl","models/model.pkl","models/xgb_model.pkl"]:
    try:
        model = joblib.load(model_name)
        print(f"Loaded: {model_name}")
        break
    except Exception as e:
        print(f"Skip {model_name}: {e}")

df = pd.read_csv(DATA_PATH)

if hasattr(model, "feature_names_in_"):
    FEATURES = list(model.feature_names_in_)
    print(f"Features from model: {FEATURES}")
else:
    EXCLUDE = {"success","id","project_id","name","category","region"}
    FEATURES = [c for c in df.columns if c not in EXCLUDE and df[c].dtype in ["float64","int64"]]
    print(f"Features auto-detected: {FEATURES}")

TARGET = "success"
X, y = df[FEATURES], df[TARGET]

y_pred = model.predict(X)
y_prob = model.predict_proba(X)[:,1] if hasattr(model,"predict_proba") else y_pred.astype(float)

acc = accuracy_score(y, y_pred)
auc = roc_auc_score(y, y_prob)
f1  = f1_score(y, y_pred)
print(f"Accuracy={acc:.4f}  AUC={auc:.4f}  F1={f1:.4f}")

try:
    explainer   = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X)
    if isinstance(shap_values, list):
        shap_values = shap_values[1]
    print("SHAP: TreeExplainer OK")
except Exception as e:
    print(f"TreeExplainer failed ({e}), using PermutationExplainer...")
    sample = X.sample(min(100,len(X)), random_state=42)
    explainer   = shap.PermutationExplainer(model.predict_proba, sample)
    shap_values = explainer(sample).values[:,:,1]
    X = sample
    y = y.loc[sample.index]
    print("SHAP: PermutationExplainer OK")

mean_abs_shap = np.abs(shap_values).mean(axis=0)
feature_importance = dict(zip(FEATURES, mean_abs_shap.tolist()))

rows = "".join([
    '<tr style="background:{};"><td style="padding:8px 18px;">{}</td><td style="padding:8px 18px;text-align:right;font-weight:bold;">{:.4f}</td></tr>'.format(
        "#f1f8e9" if i%2==0 else "#fff", feat, val
    )
    for i,(feat,val) in enumerate(sorted(feature_importance.items(),key=lambda x:-x[1]))
])

metrics_html = """
<hr>
<h2>Model Performance Metrics</h2>
<p style="color:#555;font-size:14px;">Classification | n={} samples | target: project success</p>
<table style="border-collapse:collapse;width:520px;font-family:Arial,sans-serif;">
  <thead><tr style="background:#2e7d32;color:#fff;">
    <th style="padding:10px 18px;text-align:left;">Metric</th>
    <th style="padding:10px 18px;text-align:right;">Value</th>
    <th style="padding:10px 18px;text-align:left;">Note</th>
  </tr></thead>
  <tbody>
    <tr style="background:#f1f8e9;"><td style="padding:9px 18px;">Accuracy</td><td style="padding:9px 18px;text-align:right;font-weight:bold;">{:.4f}</td><td style="padding:9px 18px;color:#555;">% correct predictions</td></tr>
    <tr><td style="padding:9px 18px;">AUC-ROC</td><td style="padding:9px 18px;text-align:right;font-weight:bold;">{:.4f}</td><td style="padding:9px 18px;color:#555;">1.0 = perfect classifier</td></tr>
    <tr style="background:#f1f8e9;"><td style="padding:9px 18px;">F1-Score</td><td style="padding:9px 18px;text-align:right;font-weight:bold;">{:.4f}</td><td style="padding:9px 18px;color:#555;">Precision/Recall balance</td></tr>
  </tbody>
</table>
<h2 style="margin-top:32px;">Feature Importances (mean |SHAP|)</h2>
<table style="border-collapse:collapse;width:400px;font-family:Arial,sans-serif;">
  <thead><tr style="background:#2e7d32;color:#fff;">
    <th style="padding:10px 18px;text-align:left;">Feature</th>
    <th style="padding:10px 18px;text-align:right;">Mean |SHAP|</th>
  </tr></thead>
  <tbody>{}</tbody>
</table><hr>
""".format(len(y), acc, auc, f1, rows)

report_path = REPORTS_DIR / "shap_report.html"
html = report_path.read_text(encoding="utf-8")
if "Model Performance Metrics" in html:
    print("Metrics already in report, skipping")
else:
    html = html.replace("</body>", metrics_html + "</body>")
    report_path.write_text(html, encoding="utf-8")
    print("Done: {}".format(report_path))

metrics_json = {"accuracy":round(acc,4),"auc_roc":round(auc,4),"f1":round(f1,4),
                "n_samples":len(y),"n_features":len(FEATURES),
                "feature_importance":{k:round(v,4) for k,v in feature_importance.items()}}
(REPORTS_DIR/"model_metrics.json").write_text(json.dumps(metrics_json,indent=2))
print("Done: reports/model_metrics.json")
