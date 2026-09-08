# Thesis Artifacts — Screenshots

> **В этом каталоге нет ни одного снимка** — он содержит только этот файл.
> Ниже названо семь имён файлов; шесть не существуют нигде в репозитории,
> седьмое, `02-evaluate.png`, лежит в `assets/screenshots/` и принадлежит
> другому набору — демонстрационному (`docs/DEMO.md`), где под тем же номером
> идёт другой экран.
>
> Файлов `docs/screenshots/*.png` **не было ни в одном коммите**: проверено по
> всем веткам и тегам (`git log --all --diff-filter=AMD --name-only`), и
> поиском по этой машине их тоже нет. Это не порча при правке — они не были
> добавлены. Описания при этом содержат измеренные значения («баннер
> "Baseline fitted: 734 samples"», «p-value < 0.0001 для 4 фич»), то есть
> снимки существовали где-то вне Git. Восстановить их из репозитория нельзя.
>
> Тексты разделов сохранены как есть: они описывают экраны, которые платформа
> действительно отдаёт, и годятся как спецификация, если снимки будут сделаны
> заново. Пересъёмка не воспроизведёт подписи дословно — база, на которой
> измерялись «734 samples» и «11 MLflow events», уничтожена вместе с прежним
> сервером 2026-08-16. Новый снимок под старой подписью был бы выдумкой. См.
> #297.

Каталог визуальных артефактов SORA.Earth Platform для главы "System overview" диплома.

## 1. 01-home.png — Home dashboard
Лендинг платформы: операционный статус, headline KPI, навигация по модулям.

## 2. 02-evaluate.png — ESG evaluator
Core ML use-case: ввод параметров проекта (budget, CO2, social, duration) -> ensemble inference (RF + Stacking + Calibrated) -> ESG score + risk class.

## 3. 03-explain.png — SHAP local explanation
Interpretability layer: per-feature SHAP contributions для конкретного предсказания. Закрывает требование XAI / Trustworthy AI.

## 4. 04-mlops-control.png — MLOps Control Room
Сводный экран наблюдаемости: 4 KPI (Active modeto / mlops_auto / manual_test) со статусами success / rejected; 9 feature importance bars (budget доминирует, year/quarter ~ 0).

> **Раздел 5 утрачен.** Его текст затёк в раздел 4: «4 KPI (Active modeto /
> mlops_auto / manual_test)» — это два описания, сросшиеся в одно, и «Active
> modeto» тоже оборвано. Тему определить нечем (#296).

## 6. 06-drift-stable.png — Drift baseline (STABLE)
Состояние после Fit baseline через UI: 7 features в LOW severity, |z| <= 0.04, баннер "Baseline fitted: 734 samples".

## 7. 07-drift-temporal.png — Drift temporal trend (KS-test + MLflow timeline)
Thesis-grade артефакт: line chart drift_score по 11 MLflow events (видимый dip 1.0 -> 0.5 -> 1.0), threshold 0.31, per-feature Kolmogorov-Smirnov таблица с p-value < 0.0001 для 4 фич, methodology footer (H0, alpha = 0.01).

## 8. 08-calibration-ensemble.png — Cross-Model Trust
Three independent models (rf_v1, stacking_v2, calibrated_v2) vote on the same project. Recommendation banner (CONSENSUS / MODERATE / HIGH DISAGREEMENT), 4 KPI (Weighted proba, Tree CI 90%, Tree std, N trees), bar chart per-model probability с consensus re

> **Здесь обрыв.** Хвост раздела 8 кончается на «consensus re», и дальше без
> разрыва шёл текст раздела 9: «…ion quality on synthetic dataset». Шов виден,
> сколько слов пропало между половинами — нет. Раньше эти два описания стояли
> одним абзацем под заголовком раздела 8, и раздел 9 не читался как
> отсутствующий: пропуск в конце нумерации не оставляет дырки в
> последовательности заголовков.

## 9. Calibration quality
> **Имя файла утрачено вместе с заголовком.** Номер (`09`) и тема
> («Calibration quality — Brier + ECE + Murphy») заданы таблицей ниже, поэтому
> раздел восстановлен. Слаг в имени `09-*.png` не определён ничем, и придумать
> его значило бы дать несуществующему файлу конкретное имя — та же подделка,
> из-за которой в #282 в списке из пяти моделей оказались две несуществующие.

Calibration quality on synthetic dataset (Perfect / Moderate / Biased сценарии): Brier score, ECE, Murphy decomposition (Reliability + Resolution + Uncertainty), reliability diagram с диагональю y = x.

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
