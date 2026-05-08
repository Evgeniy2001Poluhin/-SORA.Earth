# Morning plan — 2026-05-09

## DONE tonight (00:48 MSK) ✅
- MLflow server локально: 127.0.0.1:5556 (sqlite:///mlflow_local.db, ./mlruns_local)
- train_model_v2.py: RF 0.9892 / XGB 0.9856 / Stacking 0.9917
- Registry: esg-success-predictor v1 → Production
- Run: e0d16d823c6b4184a943efe122d8638d

## Patches applied to train_model_v2.py
- GradientBoosting -> HistGradientBoostingClassifier(max_iter=300, max_depth=4, lr=0.05)
- dropna(subset=["success"]) + astype(int) before training
- regions: "NA" -> "NAM" to avoid pandas NA parsing

## TOMORROW
1. Перенос на сервер MLflow (Hetzner, порт 5000):
   - SSH tunnel: ssh -fN -L 5555:127.0.0.1:5000 sora-cf
   - export MLFLOW_TRACKING_URI=http://127.0.0.1:5555
   - python train_model_v2.py  → Registry на сервере

2. Замена синтетики на реальные данные:
   - Экспорт evaluations из Postgres (70 rows):
     docker exec sora_earth-potion,social_impact,duration_months,region,(success_probability>=0.5)::int AS success FROM evaluations WHERE success_probability IS NOT NULL) TO STDOUT WITH CSV HEADER" > /tmp/ev.csv
   - scp sora-cf:/tmp/ev.csv data/projects.csv
   - retrain v2

3. FastAPI integration:
   - /api/v1/predict → mlflow.pyfunc.load_model("models:/esg-success-predictor/Production")
   - lazy load + cache
