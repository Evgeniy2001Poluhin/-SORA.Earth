# Thesis Artifacts — Screenshots

Каталог визуальных артефактов SORA.Earth Platform для главы "System overview" диплома.

## 1. 01-home.png — Home dashboard
Лендинг платформы: операционный статус, headline KPI, навигация по модулям.

## 2. 02-evaluate.png — ESG evaluator
Core ML use-case: ввод параметров проекта (budget, CO2, social, duration) -> ensemble inference (RF + Stacking + Calibrated) -> ESG score + risk class.

## 3. 03-explain.png — SHAP local explanation
Interpretability layer: per-feature SHAP contributions для конкретного предсказания. Закрывает требование XAI / Trustworthy AI.

## 4. 04-mlops-control.png — MLOps Control Room
Сводный экран наблюдаемости: 4 KPI (Active modeto / mlops_auto / manual_test) со статусами success / rejected; 9 feature importance bars (budget доминирует, year/quarter ~ 0).

## 6. 06-drift-stable.png — Drift baseline (STABLE)
Состояние после Fit baseline через UI: 7 features в LOW severity, |z| <= 0.04, баннер "Baseline fitted: 734 samples".

## 7. 07-drift-temporal.png — Drift temporal trend (KS-test + MLflow timeline)
Thesis-grade артефакт: line chart drift_score по 11 MLflow events (видимый dip 1.0 -> 0.5 -> 1.0), threshold 0.31, per-feature Kolmogorov-Smirnov таблица с p-value < 0.0001 для 4 фич, methodology footer (H0, alpha = 0.01).

## 8. 08-calibration-ensemble.png — Cross-Model Trust
Three independent models (rf_v1, stacking_v2, calibrated_v2) vote on the same project. Recommendation banner (CONSENSUS / MODERATE / HIGH DISAGREEMENT), 4 KPI (Weighted proba, Tree CI 90%, Tree std, N trees), bar chart per-model probability с consensus reion quality on synthetic dataset (Perfect / Moderate / Biased сценарии): Brier score, ECE, Murphy decomposition (Reliability + Resolution + Uncertainty), reliability diagram с диагональю y = x.

---

## Mapping в главы диплома

| Скрин | Глава                | Методология             |
|-------|----------------------|-------------------------|
| 01-02 | System overview      | Architecture            |
| 03    | Explainability       | SHAP                    |
| 04-05 | MLOps observability  | Prometheus + MLflow     |
| 06-07 | Drift detection      | Kolmogorov-Smirnov      |
| 08    | Cross-model trust    | Ensemble disagreement   |
| 09    | Calibration quality  | Brier + ECE + Murphy    |
