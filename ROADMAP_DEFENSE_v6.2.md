# ROADMAP v6.2 — SORA.Earth Defense Sprint (UPDATED)

**Last update:** Wednesday, May 27, 2026, 8:53 PM MSK
**Защита:** ~5–10 июня 2026
**Статус:** Day 1 = 100% COMPLETE (6/6 P0 closed in 3h 01min) 🏆

---

## 📊 База — где мы СЕЙЧАС (после Day 1)

| Параметр | Значение | Источник |
|---|---|---|
| Mac локально | main @ 735c656, all green | git log |
| GitHub main | 735c656 (27 May 8:48 PM MSK) | github |
| API endpoints | 141 OpenAPI paths | curl /openapi.json |
| ML Champion | RandomForest v5 in MLflow Registry | meta_v2.json |
| AUC | 0.9892 | retrain May 27 |
| Brier score | 0.0290 | new metric |
| ECE | 0.0137 | new metric |
| Models trained | RF, XGB, Stacking (passthrough=False) | train_model_v2.py |
| Calibration | reliability_diagram.png + pr_curve.png | models/ |
| Drift alerts | Slack + Telegram + Email + JSONL fallback | app/services/alerts.py |
| LLM Co-Pilot | temures + SchedulerPanel | web/src/features/ |

---

## ✅ P0 — DONE (Day 1, 5:50 PM → 8:51 PM = 3h 01min)

| # | Task | Commit | Status |
|---|---|---|---|
| 1.1 | MLflow Registry champion in /predict/v2 | 7d9123b | ✅ DONE |
| 1.2 | Scheduler routes (4 endpoints) | 7d9123b | ✅ DONE |
| 1.3 | SchedulerPanel UI (admin) | 20e20b7 | ✅ DONE |
| 1.4 | LLM Co-Pilot (POST /copilot/explain) | 735c656 | ✅ DONE |
| 1.5 | Drift alerts (Slack+TG+Email+JSONL) | b651e6a | ✅ DONE |
| 1.6 | Stacking + reliability + PR curve | 57ef86e | ✅ DONE |

**Result:** 6/6 P0 closed = 100%. Day 1 finished 8 days ahead of original schedule.

---

## 🟠 P1 — Defense killer features (Days 2-5, ~10 h)

| # | Task | Time | Defense Impact |
|---|---|---|---|
| 2.1 | UI компонент CopilotPanel.tsx (форма + JSON viewer + live demo) | 2 h | 🔥🔥🔥 KILLER |
| 2.2 | RAG enrichment — добавить region ESG context в copilot prompt | 1.5 h | 🔥🔥🔥 |
| 2.3 | Drift alerts UI panel + список пoint /api/v1/ab/predict (champion vs challenger) | 2 h | 🔥🔥 |
| 2.5 | Real data retrain — 70 evaluations from Postgres → projects.csv v2 | 1.5 h | 🔥🔥 |
| 2.6 | OPENAI_API_KEY setup + smoke test GPT mode in Co-Pilot | 30 min | 🔥 |
| 2.7 | demo_copilot.sh end-to-end script (predict → SHAP → explain) | 1 h | 🔥 |

---

## 🟡 P2 — Polish (Days 6-8, ~6 h)

| # | Task | Time |
|---|---|---|
| 3.1 | Fix `Duplicate Operation ID default_health` warning | 5 min |
| 3.2 | Compliance UI — страница для /compliance/csrd + /gap-analysis | 2 h |
| 3.3 | Batch UI — массовые оценки через /batch/evaluate | 1.5 h |
| 3.4 | Fairness audit — bias моделей по регионам РФ | 1 h |
| 3.5 | README + API_CATALOG update (141 endpoints, AUC 0.9892) | 30 min |
| 3.6 | 10 скриншотов в thesis/figures/ | 1 h |

---

## 📅 Новый календарь (revised)

| Day | Date | Tasks | Hours |
|---|---|---|---|
| 1 ✅ | Wed 27 May | ALL P0 (1.1-1RAG + 2.6 OPENAI key | 2 |
| 4 | Sat 30 May | 2.3 Drift UI + 2.7 demo.sh | 2.5 |
| 5 | Sun 31 May | REST or buffer | — |
| 6 | Mon 1 Jun | 2.4 A/B endpoint + 2.5 real data retrain | 3.5 |
| 7 | Tue 2 Jun | 3.2 Compliance UI + 3.3 Batch UI | 3.5 |
| 8 | Wed 3 Jun | 3.4 Fairness + 3.5 docs + 3.6 screenshots | 2.5 |
| 9 | Thu 4 Jun | Smoke test + deploy to prod | 2 |
| 10-11 | Fri-Sat | Buffer / rehearsal | — |

**Total remaining:** ~18 h over 8 days = 2.25 h/day average.

---

## 🛡️ Что НЕ делаем (out of scope для защиты)

- ❌ Webhooks subscriptions
- ❌ Prophet forecast 12 мес
- ❌ Embed widget iframe
- ❌ Public status page
- ❌ Onboarding tour
- ❌ UFW firewall, Cloudflare WARP
- ❌ Production OpenAI API ($$$ — оставим mock/template для demo)

→ всё post-defense.

---

## 🏆 Что готово к защите (snapshot 27 May)

| Метрика | До спринта | После Day 1 |
|---|---|---|
| API endpoints | 125 | **141** (+16) |
| Activ 0.029, ECE 0.014** |
| Reliability diagram | none | ✅ comparing 3 models |
| PR curve | none | ✅ |
| MLflow Registry | manual | ✅ **champion alias auto-promotion** |
| Drift alerts | none | ✅ **Slack + Telegram + Email + JSONL** |
| LLM Co-Pilot | none | ✅ **template + GPT hook** |
| Killer demo features | 0 | **3** (Co-Pilot, Drift alerts, Calibration) |

---

## 🎯 Demo storyline для жюри (5 минут)

1. Open `/docs` → 141 endpoints
2. POST `/predict/v2` → probability + SHAP
3. POST `/copilot/explain` → human-readable verdict + drivers + risks
4. Open `models/reliability_diagram.png` → "model is well-calibrated, Brier=0.029"
5. POST `/drift/test-alert` → alert dispatched to 3 channels
6. Show `logs/drift_alerts.jsonl` → graceful fallback proof
7. Open MLflow UI → show champion v5 alias + lineage

---

## ⚡ Прямо сейчас — следующий шаг

**Сегодня вечером:** ОТДЫХ. Day 1 закрыт на 100% и подтверждён HTTP-тестами.

**Завтра (Day 2):** старт P1 #2.1 — CopilotPanel.tsx UI. Время: ~2.5 часа. К концу Day 2 жюри сможет увидеть Co-Pilot **в браузере**, не только в curl.

---

**Generated:** 2026-05-27 20:53 MSK
**Author:** Day 1 sprint with Claude (Sonnet 4.5)
