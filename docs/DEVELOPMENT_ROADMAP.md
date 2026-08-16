# SORA.Earth development roadmap

**Status:** CURRENT — this is the only active plan in this repository
**Adopted:** 2026-08-16 · **Base:** `ff59610` · **Decisions:** repository owner

## Why this document is singular

Five other roadmaps sit in this repository, and one of them — `ROADMAP.md` —
still announces itself as *Active development*. Three external reviews written
in July 2026 each opened with work that was already finished, and none of them
knew M2 had closed as a negative result. That is what several plans of equal
apparent authority produce: readers pick one, and plan against a project that no
longer exists.

Every superseded plan now carries a banner naming this file. They are kept, not
deleted — they record why decisions were made — but none of them is current.

`tests/test_development_roadmap_dates.py` enforces both halves of that: exactly
one document may declare itself current, and the dates below are recomputed from
the code's constants rather than trusted as prose.

## Definition of done for the project

SORA.Earth is ready when a specialist can follow any output back along the whole
chain:

```
source → observation → data snapshot → run_id → model
→ evaluation protocol → registry → promotion gate
→ active champion → forecast/decision → audit trail
```

Not when it shows many features.

## Scope

Carried forward from `ROADMAP_ENV_CRISIS_2026.md`, unchanged.

**Pilot geography.** The first version works on the existing map of Russian
regions. Not all BRICS countries at once — but the architecture must support
adding countries and regions without rewriting the models or the database.

**MVP hazards:** air pollution and extreme heat. After the MVP has confirmed
metrics: fire risk, drought, floods, and combined risks.

## Engineering principles

Also carried forward unchanged. They predate this roadmap and outlive it.

1. Audit before implementation.
2. Reuse existing architecture and conventions.
3. Do not break existing ESG endpoints.
4. Do not duplicate scheduler, database, metrics or MLOps services.
5. Use Alembic for every schema change.
6. Every new module requires unit and integration tests.
7. External API calls must use timeout, retry, cache and fallback.
8. No synthetic data may be written to production tables.
9. Synthetic data are allowed only in isolated tests and benchmarks.
10. All timestamps must be timezone-aware and stored in UTC.
11. Separate event time, publication time and ingestion time.
12. Do not use random train/test split for time-series evaluation.
13. Deep-learning models must not become champion based only on sample count.
14. Every model must beat a simple baseline.
15. Critical alerts require uncertainty and human review.
16. No deployment before tests and migration checks pass.
17. Never modify secrets or commit `.env`.
18. Make small, reviewable commits.
19. Update roadmap status after every completed task.
20. Report facts only; do not claim metrics that were not measured.

Principle 20 is why M2 closed as a negative result rather than reporting a
number, and why no forecast quality figure appears anywhere in this document.

## What this supersedes

Six plans existed in this repository, of equal apparent authority. All are now
marked historical and kept — they record why decisions were made — but none is
current.

| File | What it was | Disposition |
|---|---|---|
| `ROADMAP_ENV_CRISIS_2026.md` | Environmental Intelligence programme, phases 0–8 | Scope, principles and phase structure merged here; M1 sign-off lives in `docs/M1_DATA_TRUST_REPORT.md` |
| `ROADMAP.md` | Day 1–7 sprint to v0.2.0; declared itself *Active development* | Sprint completed; nothing outstanding |
| `ROADMAP_DEFENSE_v6.2.md` | Defence sprint, 27 May 2026, P0–P2 by day | Sprint completed |
| `ROADMAP_DEFENSE_v6.md` | Superseded by v6.2 before this document existed | Historical |
| `SORA_Earth_Roadmap.md` | v13, 15 April 2026; component status inventory | Every listed component reads ✅; superseded by the measured table below |

`sora_ai_copilot/ROADMAP.md` belongs to that module and is out of scope here.

## When this document expires

It must be revised, not quietly followed, if any of these becomes true:

- the §7 gate constants change (the guard test fails on purpose in that case);
- M3 is closed, whether positively or negatively;
- the retrain lifecycle contract in #199 is settled differently from phases 1–5;
- a phase's exit criterion is found to be unreachable, as happened to M2.

## Phases

| # | Phase | Horizon | Work | Exit criterion | Depends on |
|---|---|---|---|---|---|
| 0 | Lock governance | now | Adopt one roadmap; mark old plans historical; record base SHA, decision owner, expiry conditions | One current roadmap, guarded by tests against drift from code | — |
| 1 | Safe finalisation | 1–2 weeks | #199 Phase 0: finalise strictly by `run_id`/`log_id`; drop the "newest row" lookup; a new run always materialises the registration outcome | A competing run is provably unmodified; an ImportError does not become legacy; a finalisation error is visible | #199 review |
| 2 | Canonical run | 2–6 weeks | One `run_id`; split `training_status`, `registry_status`, `promotion_status`, `failure_reason`; remove double counting and false durations | One physical run counted once; statuses cannot contradict each other | Phase 1 |
| 3 | Model storage | 3–8 weeks | #191 together with #199 items 4/6: `seed/`, `staged/`, `active/`; keep the LFS bootstrap; define the runtime volume | Fresh clone starts; retrain does not dirty Git; losing runtime falls back to seed | Owner decision |
| 4 | Real promotion | 1–2 months | Train the candidate in staged; run one gate; activate atomically only what passed; implement rollback | A refused model never serves traffic; rollback proven by deliberate breakage | Phases 2–3 |
| 5 | One orchestrator | 1–2 months | Route all seven paths through one retrain workflow; remove the weaker alternative gate in `app/api/infra.py` | No endpoint bypasses registry, CI bound, baseline and non-degradation | Phase 4 |
| 6 | Evaluation harness | 2–6 weeks, parallel | Naive, seasonal naive, moving average; rolling-origin; MAE, MASE, bias, interval coverage, bootstrap CI, worst region | One reproducible report over one snapshot; a baseline is mandatory | Canonical run |
| 7 | Data Trust | 1–3 months | Source register, data contracts, licensing, snapshots, point-in-time lineage, completeness/freshness/coverage | Every result ties to a source and a snapshot; gaps and revisions are visible | Can run in parallel |
| 8 | M3 accumulation | until the evidence dates | Watch daily Open-Meteo coverage; record dropped days and the actual earliest evidence date | 12 admissible windows with no hidden gaps | Calendar |
| 9 | Challenger models | after phase 6 | CatBoost/EBM for tabular data; LSTM shadow only; one split and one snapshot | Challenger compared to champion and baseline over identical windows | Evaluation harness |
| 10 | Crisis benchmark | after the data is defined | Event schema, severity, negatives, lead time, evidence chain; then the crisis engine and Redis events | Event-level recall/precision, false alarms and lead time measured | Expert labelling |
| 11 | Specialist workspace | 2–6 months | Case workflow, maps, evidence panel, provenance, escalation, audit trail, CSV/PDF/API | A specialist completes signal → verified decision | UX may start earlier |
| 12 | Copilot | after tools stabilise | Citation-first RAG, tool gateway, read-only operations, audit, prompt-injection controls | Citation precision ≥ 95%; unsupported claims ≤ 2%; every number comes from tools/API | Data lineage |
| 13 | Evidence pack | before the pilot | Model cards, dataset cards, evaluation reports, security/DR, restore drill, limitations, UAT sign-off | Pack tied to a release tag, Git SHA, snapshots and models | Earlier phases |
| 14 | Limited pilot | final | Human-in-the-loop pilot with KPIs declared in advance and rollback | Pilot report and a go/no-go decision made on measurements | Evidence pack |

Phases 1–2 complete between roughly 2026-09-06 and 2026-10-11, which is well
before the first M3 evidence date. That ordering is the point: the observations
accumulating between now and February are what M3 will be argued from, and they
must be recorded under a canonical run to be aggregatable at all.

## Now

| Priority | Task | Result |
|---|---|---|
| P0 | Finish the #199 review | Run owner, statuses and the moment of activation are settled |
| P0 | #199 Phase 0 | Targeted finalisation; the registration outcome is always written |
| P0 | Measure the ambiguity window | The count of new rows without `registry_ok` is known |
| P1 | #199 Phase 1 | One run, and analytics that are correct |
| P1 | Design #191 together with staged/active | LFS bootstrap survives; retrain is separated from Git |
| P1 | Build the baseline/evaluation harness | A future forecast has something to be compared against |
| P1 | Watch M3 coverage | No calendar days are lost |
| P2 | Specialist interviews and UX scenarios | The UAT protocol exists in advance |
| P2 | Data contracts and dataset snapshots | Data is fully reproducible |

## What needs elapsed time

| Result | Not before | Condition |
|---|---|---|
| M3, horizon 7 | **2027-02-04** | 12 admissible windows and sufficient coverage |
| M3, horizon 30 | **2028-02-05** | The required long history |
| Any claim of beating a baseline | after the corresponding backtest | Rolling-origin, CI and regional slices |
| Crisis warning quality | after an event-labelled benchmark | Real events, negatives and lead time |
| Specialist assessment | after UAT | 5–10 specialists, 30–50 scenarios |

Both dates are `TRAINING_DAYS[h] + REQUIRED_WINDOWS × h` days from the M3 clock
start of 2026-08-14, with the constants in
`app/services/forecasting/entry_conditions.py`. **Every day below 80% coverage
moves them further out.** No effort brings them closer.

## Deferred until a measured need exists

Kubernetes · Redis Cluster · PostgreSQL read replicas · Feature Store · a local
LLM for its own sake · new paid APIs without a specific question · new models
before the shared evaluation harness · the crisis Redis publish before an event
contract and a consumer · bulk refactoring of the training files without a map
of their callers.

The last two are corrections to an earlier draft of this roadmap, which had both
as immediate work. Publishing crisis events with no agreed contract creates an
interface nobody consented to, and merging `train_model.py` / `_v2` / `_backup`
without mapping callers is the mistake already made once on `_do_retrain`, where
seven call sites turned out to exist where one was assumed.

## Readiness criteria

| Area | Required outcome |
|---|---|
| Data | 100% of production results tie to source, timestamp and snapshot |
| MLOps | One run, one gate, staged activation, proven rollback |
| Forecasting | 12 windows, baseline, CI, interval coverage, worst-region report |
| Crisis | Event benchmark, false-alarm rate, lead time, evidence chain |
| Security | No unaccepted Critical/High; secrets and RBAC verified |
| Recovery | A restore drill confirms RPO ≤ 24 h and RTO ≤ 4 h |
| Copilot | Every number from tools; citations and unsupported claims measured |
| Product | A specialist can verify the origin of any value |
| Governance | High/Critical decisions are confirmed by a human |
| Release | Evidence pack tied to tag, Git SHA, models and data |

## Immediate sequence

```
#199 Phase 0
→ #199 Phase 1
→ #191 + seed/staged/active
→ one gate and orchestrator
→ evaluation harness
→ challengers
→ M3 evidence
→ crisis benchmark
→ specialist UAT
→ evidence pack
→ pilot
```

The governing principle: make one model run self-consistent and reproducible
first, then widen what the models and the product can do.

## Already true — do not re-plan

Each row cites what makes it true. July's reviews proposed all of these as
outstanding work.

| Claimed outstanding | Actual state | Evidence |
|---|---|---|
| raise AUC 0.70 → 0.80 | 0.80, and the 95% CI **lower bound** is what is tested | `app/model_quality.py:67` |
| refresh 24h → 6h | 6h; ingestion hourly | `app/scheduler.py` |
| EnvironmentalObservation schema | exists, six ingesters | `app/database.py:389` |
| SHA-256 password hashing | Argon2id | `app/auth.py:9,83` |
| no automated backups | encrypted, daily, locked | `scripts/backup_crypt.sh` |
| clean Alembic install fails | passes, guarded by its own CI job | `.github/workflows/ci.yml` |

`sha256` does still appear in `app/auth.py` — as HMAC token signing, which is
correct use, not a password hash.
