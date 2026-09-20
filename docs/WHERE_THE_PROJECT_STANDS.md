# Where the project stands, and what is worth doing next

    written    2026-08-14
    supersedes ROADMAP.md, ROADMAP_DEFENSE_v6.md, ROADMAP_DEFENSE_v6.2.md,
               PROJECT_ANALYSIS_2026-07-17.md, IMPROVEMENTS_2026-07-17.md
    does not   supersede ROADMAP_ENV_CRISIS_2026.md, which remains the phase
               plan for the experiment, or the M1/M2/M3 documents, which are
               the record

This file exists because five planning documents were current at different
times and none says so. A reader picking one up finds a confident plan with no
indication that its "not done" list was finished months ago. On 2026-08-14 that
happened: a status document from July listed the observation table, both
ingesters and the Phase 0 audit as outstanding, and all three had shipped.

## 1. The two things in this repository

```
Product      SORA.Earth ESG platform -- README.md, CLAUDE.md
             In production, 163 endpoints. Punch-list empty.

Experiment   Environmental crisis analytics -- ROADMAP_ENV_CRISIS_2026.md
             Phase 1 complete. M2 closed negative. M3 clock running.
```

The experiment does not gate the product and has not been promoted. See
`CLAUDE.md` § "The product, and an experiment beside it".

## 2. What is settled, with its evidence

| | state | pinned to |
|---|---|---|
| M1 Data Trust | signed off, production verified | `docs/M1_DATA_TRUST_REPORT.md` |
| M2 Forecasting | **CLOSED — NEGATIVE RESULT** | `e82d682` |
| M3 Forecast target | declared; **§7 clock running from 2026-09-03**, restarted by measurement in amendment 1.2 | `10f985f` |
| Phase 1 (env foundation) | all seven exit criteria met | |
| Product punch-list | empty | |

M2's result is the load-bearing one: the ESG score is a structural index over
static snapshots, 1.00 distinct values per region. Against a constant the
last-value baseline is exact, so a benchmark that ran would have produced
MAE = 0 — a property of the data wearing the shape of a result about a method.

## 3. The fact that shapes everything until February

M3 cannot produce an evidential result before **2027-02-24** (h=7) or
**2028-02-25** (h=30). The clock was voided on 2026-09-02, when the production
server was deleted for non-payment and the accumulated observations were lost
with it; amendment 1.2 restarted it on 2026-09-19 at the first day on the
rebuilt deployment that meets the coverage rule — 2026-09-03, measured, with
24 of 24 hours at every one of the 21 points (2026-09-02 had 4 and fails).

The dates come from the gate's own constants —
`REQUIRED_WINDOWS = 12`, `TRAINING_DAYS = {7: 90, 30: 180}` — and no amount of
engineering moves them. The outage cost 20 days against the voided clock.

So the question for the next 174 days is not "what feature next". It is: **what
is worth doing in a window where no forecasting result can be evidential?**

Three answers, in order of value.

### A. Protect the accumulation

If accumulation breaks, the date moves. A day under 80% coverage is a day the
gate cannot use.

**Known gap:** nothing watches this. `/api/v1/ingestion/attention` reports the
verdict of each source's latest *run* — it catches "the ingester stopped", and
only weakly, since `openmeteo` has no declared `max_vintage_hours`. It does not
see a day where 18 of 24 hourly observations arrived. That is the thing that
moves the evidential date, and it is invisible in the instrument this file's author
previously said covered it.

What would cover it is a per-day, per-point count against the 19-of-24 rule.

**This is no longer hypothetical.** That count was run by hand on 2026-09-19,
over the first sixteen days of the restarted clock, and it found three days on
which a point fell below the rule while nothing reported anything:

| day | points meeting the rule | worst point |
|---|---:|---:|
| 2026-09-09 | 19 of 21 | 18 hours |
| 2026-09-10 | 20 of 21 | 18 hours |
| 2026-09-14 | 20 of 21 | 16 hours |

Counting rows instead of hours, all three days pass — which is how they went
unnoticed, and is the reason amendment 1.3 fixes the rule to hours. Three days
lost out of sixteen is inside the 80% floor and costs nothing yet; the point is
that the instrument did not see them, so a run of such days would also be
invisible.

**Half of this gap is now closed, and it is worth being precise about which
half.** `auto_observation_coverage` runs every 15 minutes in the scheduler and
publishes three gauges — `sora_observation_coverage_gap_days`,
`_days_examined` and `_points_examined`, the last two being the denominator
that tells "no day fell short" from "no day was examined". The count is
therefore queryable, graphable and alertable instead of living only behind an
admin endpoint nobody called.

What is **not** done is the alerting rule. A gauge in Prometheus still requires
someone to look, and choosing when a dip is worth waking someone for is the
same kind of decision as the five forecast alerts in #284 — the owner's, not a
side effect of adding the metric.

### B. Make the eventual measurement trustworthy — before it runs

Decisions made after results are visible are choices of results. That is why M2
has a §9 and why M3 was declared before accumulation counted.

- **M3 v1.1**: Diebold–Mariano for significance, bootstrap CI, worst-point
  reporting. The protocol has 12 windows and a seasonal-naive comparison but no
  test of whether a difference is real. Amending before any run is the safe
  case; amending after is what §9 forbids.
- **Metric per task, written down.** `MIN_AUC_THRESHOLD = 0.80` is a gate on
  ESG *classification*. It is meaningless for a temperature forecast, where the
  measure is MAE against seasonal naive. Nothing currently stops the number
  being applied across tasks.
- **A confidence bound in the promotion gate.** Today a model is promoted on an
  absolute floor plus non-degradation. 0.81 against 0.80 on a small test set is
  noise, and the gate cannot tell.

### C. Close what does not depend on the clock

`#98` certificate hook · `#26` after a release audit · `#164` period corrections
rewrite rows in place · `#156` inherited startup jobs · `#75` no consumer reads
the point-in-time accessor.

## 4. What not to do in this window

Stated because each is plausible and each would cost more than it returns.

- **Do not build the crisis engine.** Phases 3 and 5 of the roadmap — risk
  formula, alert lifecycle, crisis map — consume a forecast. There is no
  forecast. Building the consumer first produces a UI that displays a number
  nobody has measured.
- **Do not add model candidates.** CatBoost, EBM, tuned ensembles: there is
  nothing to compare them against, because no champion has been measured on an
  honest temporal split. Candidates before a measurement is how the best of
  several arbitrary runs becomes "the result".
- **Do not add data sources for their own sake.** Each adds a provenance
  surface — `event_time`, `published_at`, vintage, a temporal kind — and none
  shortens 174 days. Add one when a declared target needs it.
- **Do not restate M2 as anything but a negative result.** §10.6 of that
  protocol and a test in `tests/test_m2_closure_is_recorded.py` both forbid it.

## 5. Decisions that are a human's

- Whether the experiment is ever promoted to product. Until then the ESG
  platform is what the project is.
- Whether M3 v1.1 is adopted, and before which date.
- Whether the promotion gate should refuse on a confidence bound, which will
  reject models it currently accepts.

## 6. What would make this file stale

Any of: M3 reaching its evidential date; the punch-list gaining an item that blocks the
product; the experiment being promoted. Each is a rewrite, not an edit, and
this section is here so the next reader can tell.
