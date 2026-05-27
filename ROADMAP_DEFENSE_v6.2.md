# ROADMAP v6.2 — SORA.Earth Defense Sprint (UPDATED)

**Last update:** Wednesday, May 27, 2026, 8:53 PM MSK
**Защита:** ~5–10 июня 2026
**Статус:** Day 1 = 100% COMPLETE (6/6 P0 closed in 3h 01min)

## База после Day 1

- main @ 735c656
- API endpoints: 141 (verified via /openapi.json)
- ML Champion: RandomForest v5, AUC 0.9892
- Brier 0.029, ECE 0.014
- Drift alerts: Slack + Telegram + Email + JSONL fallback
- LLM Co-Pilot: template engine + OpenAI hook ready

## P0 DONE (Day 1)

| # | Task | Commit |
|---|---|---|
| 1.1+1.2 | MLflow Registry + Scheduler | 7d9123b |
| 1.3 | SchedulerPanel UI | 20e20b7 |
| 1.6 | Stacking + reliability + PR | 57ef86e |
| 1.5 | Drift alerts | b651e6a |
| 1.4 | LLM Co-Pilot | 735c656 |

## P1 — Days 2-5 (~10 h)

- 2.1 CopilotPanel.tsx UI (browser demo) — 2 h
- 2.2 RAG enrichment with region ESG context — 1.5 h
- 2.3 Drift alerts UI panel — 1.5 h
- 2.4 A/B endpoint /api/v1/ab/predict + UIaluations from Postgres) — 1.5 h
- 2.6 OPENAI_API_KEY setup + GPT mode test — 30 min
- 2.7 demo_copilot.sh end-to-end script — 1 h

## P2 — Days 6-8 (~6 h)

- 3.1 Fix Duplicate Operation ID warning — 5 min
- 3.2 Compliance UI page — 2 h
- 3.3 Batch UI — 1.5 h
- 3.4 Fairness audit — 1 h
- 3.5 README + API_CATALOG update — 30 min
- 3.6 10 screenshots in thesis/figures/ — 1 h

## Calendar

| Day | Date | Tasks | Hours |
|---|---|---|---|
| 1 DONE | Wed 27 May | All P0 | 3h 01min |
| 2 | Thu 28 May | 2.1 + 3.1 | 2.5 |
| 3 | Fri 29 May | 2.2 + 2.6 | 2 |
| 4 | Sat 30 May | 2.3 + 2.7 | 2.5 |
| 5 | Sun 31 May | REST | - |
| 6 | Mon 1 Jun | 2.4 + 2.5 | 3.5 |
| 7 | Tue 2 Jun | 3.2 + 3.3 | 3.5 |
| 8 | Wed 3 Jun | 3.4 + 3.5 + 3.6 | 2.5 |
| 9 | Thu 4 Jun | Smoke + deploy | 2 |

Total remaining: ~18 h over 8 days = 2.25 h/day average.

## Achievements vs v6.1 plan

| Metric | Before | After Day 1 |
|---|---|---|
| API endpoints | 125 | 141 |
| Champion AUC | 0.91 | 0.9892 |
| Calibration | none | Brieerts | none | 3 channels |
| LLM Co-Pilot | none | template+GPT |

Generated: 2026-05-27 20:55 MSK
