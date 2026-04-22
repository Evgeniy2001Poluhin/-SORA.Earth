---
marp: true
theme: default
class: invert
paginate: true
---

# SORA.Earth
## Production-grade ML platform for ESG project evaluation

Evgeny Poluhin · April 2026 · investor-demo ready · 90/100

---

## The Problem

- ESG investing is a $50T market by 2026
- 73% of ESG ratings are inconsistent between providers
- Investors make multi-million decisions on opaque, black-box scores
- No standardized, auditable, explainable ESG evaluation infrastructure exists

---

## The Solution — SORA.Earth

An ML-powered backend that scores any ESG project in under 200ms with full explainability, drift monitoring, and autonomous retraining.

- ROC-AUC 0.912 · F1 0.838 · Accuracy 0.823
- 15 production admin surfaces · FastAPI + PostgreSQL + XGBoost ensemble
- SHAP-based explainability · Monte Carlo risk simulation
- Self-healing MLOps via autonomous AI Teammate agent

---

## Product — Score any project in seconds

Input: budget, CO2 social impact, duration, country
Output: ESG score / 100, category breakdowns (Env/Soc/Econ), ML success probability, SHAP waterfall

Example: Solar Panel Germany · 50k USD · 85 t/yr CO2
→ Score 64.2 / 100 · probability 92.0%
→ Top drivers: efficiency_score (+0.13), budget_per_month (+0.08), social_impact (+0.08)

---

## Risk simulation — Monte Carlo

- 1,000 simulations per project in under 1 second
- P5 / P50 / P95 confidence bands on every score
- Risk distribution: LOW / MEDIUM / HIGH with probabilities
- Investor sees not just a point estimate — the full uncertainty envelope

---

## Portfolio view

20 projects evaluated · 14 Strong · 6 Medium · 0 Weak · Avg ESG 79.3

Country ranking across 30 nations — Sweden #1 (HDI 0.947, 60% renewable), USA #35, Russia #58

---

## Autonomous MLOps — AI Teammate

An agent that monitors data freshness, model health, drift, and failures — then acts without human-in-the-loop.

- 12 retrains logged · 8 successful · last retrain 13h ago
- Auto-r model, promotes versions
- Decision feed: "OK: All systems healthy. No action needed."
- Zero human intervention to keep the model fresh

---

## Production rigor

| Subsystem | Metric |
|---|---|
| Ensemble CV AUC | 0.9186 |
| Best threshold | 0.48 (optimized F1) |
| Train / test | 583 / 146 samples |
| Data health | 0% null · 0 out-of-range (24h) |
| Avg latency | 110ms |
| Scheduler | Running · 0 job failures |
| Total predictions | 327 logged · full audit trail |

---

## Observability stack

Every event, prediction, and retraining run is logged, queryable, and audit-safe.

- Activity timeline — system-wide event feed
- Audit log — every admin login + API call with IP + status
- Predictions log — 100 last inferences with latency + score
- Data health — null rates + range violations per field
- Batch evaluations — bulk scoring runs with success/fail

---

## Why now

- Regulators (EU SFDR, SEC climate rule) demand auditable ESG scores by 2027
- LLM-only "AI ESG" tools hallucinate — ours ialibrated probabilities + SHAP
- Data partnerships available: World Bank, IEA, EDGAR
- Infrastructure shift from Excel-based ESG consulting to API-first platforms

---

## Traction & Ask

Today:
- 15 production surfaces · 90/100 readiness score
- Full CI/CD · Docker · Postgres + pgbouncer · Prometheus-ready
- Academic validation (MSUPE thesis) on signal-processing foundations

Ask: seed round for
- Data partnerships
- GTM — 3 design partners in EU climate-tech funds
- 2 senior ML + 1 frontend hire

---

## Live demo available now

evgenijpoluhin · sora.earth · Amsterdam, NL

"Investing in climate without auditable ML is investing blind."

Thank you.
