# Development roadmap

**Status:** active · **Written:** 2026-08-16 · **Base:** `ff59610`

This exists because three external reviews of the project, dated 2026-07-17 and
2026-07-24, all proposed roadmaps whose first items were already done, and none
of which knew that M2 had closed as a negative result. A plan is only useful if
its starting position is measured.

Every "already true" claim below cites the file that makes it true, and the two
dates are guarded by `tests/test_development_roadmap_dates.py`, which recomputes
them from the code's own constants rather than trusting this prose.

## The one distinction the plan is built on

Work here is blocked by one of three different things, and they must not be
mixed:

- **Blocked by us** — more effort finishes it sooner.
- **Blocked by the calendar** — effort changes nothing. Evidence needs elapsed
  observation time.
- **Blocked by a decision** — the owner's, not an engineering question.

Most published roadmaps for this project mixed the second kind into weekly
sprints. That is how a plan comes to promise a forecasting result in six weeks
when the declared protocol cannot produce one before February 2027.

## Already true — do not re-plan these

| Claim in earlier reviews | Actual state | Evidence |
|---|---|---|
| "AUC threshold 0.70, raise to 0.80" | 0.80, **and** the 95% CI lower bound is what is tested, not the point estimate | `app/scheduler.py`, `app/model_quality.py:67` |
| "Data refresh 24h → 6h" | 6h; ingestion hourly | `app/scheduler.py` |
| "EnvironmentalObservation schema not done" | Exists, with six ingesters | `app/database.py:389`, `app/ingesters/` |
| "SHA-256 password hashing (HIGH)" | Argon2id, argon2-cffi defaults | `app/auth.py:9,83` |
| "No automated backups (CRITICAL)" | Encrypted daily backups with locking | `scripts/backup_crypt.sh`, `scripts/backup_local_daily.sh` |
| "Clean Alembic install fails (P0)" | Passes, and is guarded by its own CI job | `.github/workflows/ci.yml` |
| "496 tests, 72.5% coverage" | 2170 passed, 200 skipped on the CI-equivalent run | measured 2026-08-16 |

`sha256` still appears in `app/auth.py`, in HMAC token signing. That is correct
use and not a password hash; the review that flagged it read the name, not the
call site.

## Blocked by us — urgent

Urgent for one reason: these corrupt data that is accumulating **now**.
Everything measured between today and the M3 dates becomes the evidence for M3.
A journal that cannot say what one run did makes those measurements
unaggregatable, and they cannot be re-taken because the time will have passed.

| # | Step | What it closes | Done when |
|---|---|---|---|
| A1 | #199 Phase 0 — finalise by `run_id` | `app/api/infra.py` updates "newest row with this `trigger_source`", so a concurrent run finalises the wrong one | A competing row with the same `trigger_source` is provably untouched |
| A2 | #199 Phase 0 — total write of the registry outcome | An `ImportError` leaves `registry_ok` absent, which reads as "recorded before the contract" and is promoted | A new run always materialises `True` / `False` / `registry_error` |
| A3 | Measure the ambiguity window on production | Rows since 2026-08-15 that lost the key cannot be classified retrospectively | Four counts recorded in #199 |
| A4 | #199 Phase 1 — one `run_id` per run | Two journal rows per cycle, related by nothing but a timestamp | One run, one id; `app/api/admin_ai.py` counts a cycle once |
| A5 | #199 Phase 1 — separate the statuses | `success` sits beside `registry_ok=False` in one row | `training_status` / `registry_status` / `promotion_status` / `failure_reason` are columns |
| A6 | Close #189; carry the tests over from #198 | The false `success` stops being written on **all seven** call paths | #189 closed; the legacy-row test and its mutation check moved, not rewritten |

## Blocked by us — not urgent, waiting on nothing

| # | Step | Why | Done when |
|---|---|---|---|
| B1 | Baseline ladder: naive, seasonal naive, moving average | Without it, February 2027 arrives with a model and nothing to compare it against | Every forecast shows a baseline beside it; the harness runs on today's data |
| B2 | CatBoost as challenger | Nine features with categoricals (country, region) is its case; a neural net is not the first candidate at this width | Installed, trained, compared to RF on one split |
| B3 | Merge `train_model.py` / `_v2` / `_backup` | Three sources of truth about training | One file; suite green |
| B4 | Publish crisis events to Redis | `app/services/crisis_detector.py` contains zero `publish` calls — events reach nobody | A subscriber receives one, and a test proves it |
| B5 | Specialist UAT | Does not depend on model quality: it tests workflows, not numbers | 5–10 specialists, 30–50 cases, signed report |
| B6 | Cover `app/training.py` and `app/shap_explainer.py` | 0% on the modules that decide quality | Tests that go red under mutation |

**B1 is the one that gets deferred and should not be.** Baselines are cheap now
and impossible to backfill into a comparison that has already been published.

## Blocked by the calendar

| What | Earliest date | What moves it |
|---|---|---|
| M3 forecast evidence, horizon 7 | **2027-02-04** | nothing that is worked on |
| M3 forecast evidence, horizon 30 | **2028-02-05** | nothing that is worked on |
| Either date, in the wrong direction | later | every day below 80% hourly coverage |

Both are `TRAINING_DAYS[h] + REQUIRED_WINDOWS × h` days from the clock start of
2026-08-14, with the constants in
`app/services/forecasting/entry_conditions.py`.

The only work that changes these dates is watching coverage — `/observations/coverage`,
#185 — and not losing days. No forecast quality figure may be published before
its date; one named earlier would have to be withdrawn, which is the single
most expensive thing this project can do to itself.

## Blocked by a decision

| What | Why it is the owner's call |
|---|---|
| #199 items 4 and 6 together with #191 | Both decide where model artefacts live; deciding separately means choosing the layout twice |
| #164 `as_of_date` | Repeated correction is declared behaviour; a backfill bypass has to be intended, not inferred |
| Exposing the registration retry on a route | New outward surface |
| Local LLM / Ollama | Changes the deployment profile |
| Read replicas, Redis Cluster, Kubernetes | Recommended to defer: the backend runs at under 1% CPU and there is no measured bottleneck |

## Sequence

| Window | Block | Result |
|---|---|---|
| now → 2 weeks | A1–A3 | Finalisation is targeted, errors are visible, the ambiguity window is a measured number |
| 2 → 6 weeks | A4–A6 | One canonical run; #189 closed; per-run analytics become correct |
| in parallel, 1 → 8 weeks | B1–B6 | Baselines, CatBoost, one training path, crisis events delivered, UAT |
| 2 → 6 months | decisions above | Artefact layout, one gate, one orchestrator |
| **2027-02-04** | calendar | First admissible statement about forecast quality at horizon 7 |
| **2028-02-05** | calendar | The same at horizon 30 |

## What counts as success

Not a headline metric. M2 is what happens when a target is chosen before
checking that it is reachable: the ESG score turned out to have 1.00 distinct
values per region, so any AUC reported against it would have been a number about
nothing.

1. Every prediction resolves to a `run_id`, a data snapshot, a model version and
   a Git SHA.
2. No model serves traffic without having passed one gate.
3. By 2027-02-04 there are twelve honest windows and a baseline beside every
   figure.
4. A specialist can open the source of any value.
5. **No published figure has had to be withdrawn.**

The fifth is the cheapest to achieve and the most expensive to lose, and it is
bought entirely by not naming numbers before their evidence exists.
