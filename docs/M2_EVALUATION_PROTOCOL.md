# M2 Forecasting — evaluation protocol (pre-registration)

```text
Protocol            preregistered, version 1.0
Target readiness    FAILED
Reason              canonical temporal unit not established;
                    insufficient non-overlapping origins
Model runs          none performed
```

**Version:** 1.0 · **Registered:** 2026-08-08 MSK (2026-08-07 UTC)
**Supersedes:** nothing
**Evidence commit:** `1b0aacc` (production), schema `c58e21a9f7d4`
**Audit source:** #75 · **Canonical target task:** #114

This document is written **before** any model is run, and before the data it
describes exists. That is the point. A protocol written after seeing results
cannot be distinguished from a description of them.

Every choice that could be made differently once results are visible is fixed
here. Where a value is left to a later version, it is named as such and the
version bump is required before the first run — never after it.

---

## 0. Why this exists in a failed state

M2 was scoped to answer: *does a forecasting model beat a naive baseline on the
platform's ESG score, and does GDP growth help?*

The day-one target audit (§6) found that the question cannot be asked of the
data the platform currently holds — not because there is too little of it, but
because **the series is not a measurement of anything that evolves.** Section 1
states the target contract that would make the question answerable, §7 states
the conditions under which the first run is permitted, and no run is permitted
before then.

Registering the protocol now, in a failed state, is deliberate: the entry
conditions are fixed while the outcome is unknowable, so meeting them is a
check rather than a judgement call.

---

## 1. Target contract

### 1.1 What the target must be

A **canonical regional score snapshot** — one scheduled, append-only record per
region per day, written independently of user activity.

```text
region_id            from the declared set (§1.4)
observed_date        the day the state refers to
known_at             when the value became knowable (= write time)
input_snapshot_id    identity of the inputs
inputs               the inputs themselves, in full
total_score          the value
calculation_version  which scoring code produced it
```

**Uniqueness:** a database constraint on `(region_id, observed_date)`. Without
it a retried scheduler run produces two records for one region-day, and the
per-region-day weighting of §3.1 silently double-counts it.

**Retries are idempotent.** A re-run for a region-day that already has a record
is a no-op and is counted as such, not an error.

**Backfill:** writing a region-day that already exists is refused. A value may
be superseded only by a record carrying a different `calculation_version`, and
both are retained — the original is never overwritten. Reprocessing under the
*same* `calculation_version` is refused, because it cannot be distinguished
from a duplicate.

Two further requirements the audit showed are not decorative:

- **`inputs` must be stored, not only referenced.** Today part of the actual
  input to the score is discarded (§6.4), so a stored row cannot be reproduced
  from what is stored beside it. An `input_snapshot_id` pointing at nothing
  retained does not fix this.
- **Append-only must be enforced in the database**, not by convention. The
  precedent exists: `trg_cih_value_written_once`, migration `a91d7c4e28b6`.
  Today 54 of 227 ids in `evaluations` are absent (§6.6) and an endpoint
  clears the table wholesale.

With a scheduled writer, a **missing day means a missed observation** — a fact
about the platform. Today a missing day means nobody used the form, which is a
fact about visitors.

### 1.2 The open question about `Evaluation`

`Evaluation` **is not this target and cannot be adapted into it.** The audit is
in §6; the finding in one line: `total_score` is a deterministic function of an
HTTP request payload, so the series records what users typed, not what the
world did.

The existing 173 rows are retained and remain useful for:

- descriptive audit,
- API and regression testing,
- possibly cross-sectional analysis, *with* the caveat in §6.4 that the stored
  columns do not determine the stored score.

They are **not** to be presented as 173 temporal observations, and no fold in
this protocol may draw on them.

### 1.3 Units

`total_score` is on a 0–100 scale. All errors are reported in **score points**,
never as percentages of the score (§3.3).

### 1.4 Regions

The declared region set is **not yet fixed**, because it depends on the design
decision in #114. It is fixed by a version bump to this document, which is
required **before the first snapshot is written** — not before the first model
run, because the coverage denominators of §5 and §7 are meaningless without it.

What is fixed now, and cannot be revisited:

- The set is an **explicit enumeration of `region_id` values with a mapping
  version**, recorded in the version that declares it.
- A record whose `region_id` is outside the declared set is **rejected at
  write time**, not filtered later.
- The **coverage denominator everywhere in this document** — §5, §7 — is
  `|declared regions| × |days in window|`. It is never the set of regions that
  happen to have data, which would be selection on the outcome.
- Regions are neither added nor removed once declared, except by a version
  bump that restarts the §7 clock for any region it adds.

This matters because the current `region` field is derived from user input with
a default: `Europe` holds 167 rows across 14 days and `North America` holds 6
rows across 4 days. Inheriting that set would inherit the selection.

---

## 2. Vintage modes

Both modes are defined now; which are reported is fixed here, not chosen later.

### 2.1 Strict real-time — primary for any real-time claim

Only rows with `fetched_at <= origin` are visible at an origin. Legacy rows
whose `fetched_at` predates the vintage-tracking era are **excluded**, not
back-dated.

`known_at = fetched_at`. The strict feature builder must **refuse to read the
latest row** by construction, not by filtering after the fact.

Eligibility begins at the first date from which vintage history is complete.
For external indicators that is **2026-08-05**; earlier origins are not
eligible for strict mode and no amount of care makes them so.

### 2.2 Pseudo-real-time — labelled experiment, never a real-time claim

A single fixed data vintage, truncated by `observed_period`. This answers "how
would the model score with today's data arranged by period", which is a
different and weaker question.

**The vintage must be identified, not merely described as "current".** Before
the first run, record and freeze:

- the **vintage cutoff timestamp** — the `fetched_at` ceiling applied uniformly
  to every source,
- a **per-source snapshot identifier**: for `country_indicator_history`, the
  maximum `fetched_at` and row count per `(source, indicator_code)`; for any
  other input, whatever identifies its version,
- the **schema revision** and the **snapshot commit SHA**.

These identifiers appear in **every table, figure and summary** that reports a
pseudo-real-time result, alongside the label. A result that cannot name its
vintage cannot be reproduced and is not a result.

Any such result carries the label in every table, figure and summary. It may
not be described as backtesting, historical performance, or real-time accuracy.

### 2.3 The formulation to be used in reports

> Historical real-time performance cannot yet be estimated because vintage
> history begins on 2026-08-05. Results labelled pseudo-real-time use a single
> fixed data vintage and therefore overstate what was knowable at each origin.

Future revisions are preserved rather than overwritten, so this eligibility
date moves forward only by the passage of time.

---

## 3. Metrics

### 3.1 Primary

**Macro MAE across regions**, in score points: MAE computed per region, then
averaged with equal weight per region. A region with more observations does not
count for more.

Within a region, **one weight per region-day** after aggregation. This is the
rule that prevents a single busy day from dominating the loss, which on the
current data would hand 48% of the weight to 2026-07-13.

### 3.2 Secondary

Reported alongside the primary metric, never instead of it.

**RMSE**, same aggregation as §3.1.

**MASE**, with a scaling denominator defined **independently of the baselines
in §4** — the two are not the same object and conflating them makes the metric
uninterpretable:

- For each region and each fold, the denominator is the **mean absolute
  first difference of the target over that fold's training slice**, computed on
  consecutive *observed* region-days only. Gaps are skipped rather than
  interpolated; a difference spanning a gap is still used, and the gap length
  is not used to rescale it.
- The denominator uses the **training slice only** — never the test window, and
  never the full series.
- **If the denominator is zero** (a region whose training slice is constant, or
  has fewer than two observed days), MASE is **undefined** for that
  region-fold. It is excluded from the MASE aggregate, the exclusion is
  counted, and the count is reported next to the metric. It is not replaced by
  a small constant and the region is not dropped from the primary metric.

### 3.3 Prohibited

**MAPE is not a primary metric and is not a tie-breaker.** It is asymmetric,
undefined at zero, and rewards under-prediction. It may appear only as a
descriptive figure, clearly marked as such.

---

## 4. Baselines

Both are fixed here, before any model exists.

**1. Last value.** Carry the most recent observed value for that region
forward across the horizon. If a region has no observed value at or before the
origin, that region-fold is excluded from every metric and the exclusion is
counted.

**2. Trailing mean.** The mean of observed values for that region over the
**28 calendar days ending at the origin, inclusive**, over observed region-days
only.

- The window is **28 days for both horizons**. It is not tuned, not varied per
  horizon, and not selected by performance. If it turns out to be a poor
  choice, that is a result about the baseline.
- **Minimum 4 observed region-days** in the window. Below that, the baseline
  emits the last-value forecast for that region-fold, and the substitution is
  recorded and counted in the results table.
- If fewer than 1 observation exists, the region-fold is excluded as above.

A model that does not beat **both** baselines on the primary metric has not
succeeded, whatever its secondary metrics show.

---

## 5. Folds

**Expanding window, rolling origin.** The training slice for a fold is every
observed region-day at or before its origin; it grows as origins advance.

### 5.1 Deterministic construction

Fixed here so that no window is chosen after results are visible.

1. Let `S` be the first `observed_date` in the canonical series and `E` the
   last `observed_date` for which the full horizon `h` is available
   (`E = last observed_date − h`).
2. **The last window ends at `E`.** Windows are then laid **backwards** from
   `E` in contiguous blocks of `h` days: window *k* covers
   `[E − k·h + 1, E − (k−1)·h]` for `k = 1, 2, …`. Blocks are contiguous and
   therefore non-overlapping by construction.
3. Its origin is the day before the window starts. The initial training
   requirement of §7 applies to the **earliest** window used: it is admitted
   only if its origin is at least 90 days (h=7) or 180 days (h=30) after `S`.
4. **Exactly 12 windows are used: `k = 1 … 12`** — the twelve most recent
   admissible ones. If fewer than 12 are admissible, the entry condition of §7
   is not met and no run occurs. If more are available, the older ones are not
   used, so a longer series does not change how many windows a verdict rests
   on.

Laying windows backwards from the end, rather than forwards from the start,
means adding data does not re-cut every existing window.

Horizons **h = 7** and **h = 30** are evaluated separately, with their own
window sets, and are never pooled.

### 5.2 Coverage

Coverage for a window is `observed region-days / (declared regions × h)`,
using the declared set of §1.4 as the denominator.

- It is evaluated **per region** as well as overall. The §7 threshold of 80%
  must hold **for every declared region in every window used**, not on the
  average — an average hides one region carrying nothing.
- A window failing the per-region threshold is **not** replaced by an older
  one. The entry condition simply is not met.
- Coverage is reported beside every metric it produced.

### 5.3 Missing region-days

A region-day with no observation is a **miss**: not a zero, not an
interpolation, not a carried-forward value.

- It contributes **no term** to that region's MAE. The region's MAE is the mean
  over its observed test days only.
- It **does** count against coverage, which is why coverage is reported beside
  the metric: a low-coverage region can post a flattering MAE on the few days
  it has.
- A region with **zero** observed days in a test window is excluded from that
  fold's macro average, and the exclusion is counted and reported.

### 5.4 Determinism

Seeds are fixed and recorded. Any stochastic component reports across seeds
rather than at one. The fold construction above contains no random element.

---

## 6. The audit that put this protocol in a failed state

Read-only, on production `1b0aacc`, 2026-08-08 MSK (2026-08-07 UTC). Nothing
was run or changed.

### 6.1 The series as it stands

```text
observations            173      (all with a score)
span                    2026-06-15 … 2026-08-05   =  51 days
days with data          14
regions                 2        (Europe 167 rows / 14 days;
                                  North America 6 rows / 4 days)
```

### 6.2 Folds available, counted

| horizon | arithmetic max | windows with data | usable as test |
|---|---|---|---|
| h=7 | 7 | 5 | **4** |
| h=30 | **1** | 1 | **0–1** |

```text
06-15 → 06-21   40 obs, 4 days      07-13 → 07-19   83 obs, 1 day
06-22 → 06-28   13 obs, 3 days      07-20 → 07-26    0 obs   empty
06-29 → 07-05    6 obs, 2 days      07-27 → 08-02    0 obs   empty
07-06 → 07-12   27 obs, 3 days
```

Maximum training window: 14 days — the entire series. One fold cannot yield a
variance estimate, so at h=30 "better than baseline" is indistinguishable from
"different on one window".

### 6.3 The series is not a measurement

The decisive finding, and the reason no volume of this data would suffice.

`Evaluation` rows are written in exactly one place: the `POST /evaluate`
request handler, `app/api/evaluate.py:151`. The scheduler only reads them
(`app/scheduler.py:499`). There is no scheduled writer anywhere.

`total_score` is computed by `calculate_esg(project, region)` or
`_macro_esg_from_payload(payload)` — both **deterministic functions of the
request payload**. No variable of world state enters either: not the date, not
country data, not anything that changes.

Forecasting this series is forecasting what users will type into a form. That
is a legitimate object of study — demand telemetry — but it is not the regional
ESG trajectory M2 was scoped to forecast.

A cache compounds it: an identical payload within TTL returns early and
**writes no row**. Row presence is therefore a function of cache behaviour as
well as of user activity.

### 6.4 Stored columns do not determine the stored score

One input tuple appears with two different scores:

```text
150000 | 340 | 9 | 18 | Europe  →  82    on 06-15, 06-18 ×2, 07-08, 07-09 ×2, 08-05
150000 | 340 | 9 | 18 | Europe  →  69.5  on 06-29
```

Not a calculation-version change — it returns to 82 afterwards. It is the
second code path: `_macro_esg_from_payload` fires when macro keys
(`co2_per_capita`, `renewable_share`, `gdp_per_capita`, …) are present in the
payload, and **those keys are never persisted**. The table is not
self-describing; a row cannot be reproduced from what is stored.

This is why §1.1 requires the inputs themselves, not an identifier.

### 6.5 Repetition and the entity question

```text
rows                                          173
distinct input tuples                         105     → 68 rows are exact repeats
distinct project names                          1     → no entity key exists
input tuples appearing on more than one day     5     → deterministic, so their
                                                        change over time is zero
```

2026-07-13 carries 83 rows with **83 distinct inputs**, against a typical day
of 2–16. That is a one-off sweep, not a day of activity. Days such as 06-19
(6 rows / 2 inputs), 06-22 (6/2) and 07-09 (20/7) are the repeated ones.

Counting either kind as independent temporal observations is pseudoreplication:
the nominal sample size grows while the number of independent units does not.

### 6.6 The record is mutable

```text
rows 173 | min id 1 | max id 227 | missing ids 54
```

24% of the id span is absent, and `DELETE /history/{id}` and `DELETE /history`
remain available. A target series that can be deleted is not a record of
anything.

---

## 7. Entry conditions — the gate on the first run

Preregistered. These apply to the **canonical daily target of §1**, whose clock
starts at its first scheduled write. They do not apply to `Evaluation`, which
can never satisfy §1 regardless of volume.

| condition | h=7 | h=30 |
|---|---:|---:|
| initial training window before the earliest window used | ≥ 90 calendar days | ≥ 180 calendar days |
| non-overlapping test windows | exactly 12 | exactly 12 |
| coverage, **per declared region, in every window** | ≥ 80% | ≥ 80% |
| regions with the full set of folds | all declared | all declared |
| weighting | one weight per region-day | one weight per region-day |
| declared region set (§1.4) | fixed by version bump | fixed by version bump |

Twelve is not a mathematical guarantee. It is a minimum operational gate chosen
in advance, sufficient to estimate the spread of loss across origins. Five
30-day folds were considered and rejected as too fragile to support a verdict.

**What this costs in calendar time**, from the first snapshot write:

```text
h=7     90 + 12 × 7  =  174 days  ≈   6 months
h=30   180 + 12 × 30 =  540 days  ≈  18 months
```

h=7 is the near-term objective. h=30 is declared and remains declared, but it
is a 2028 horizon and should be planned as one.

---

## 8. Model and ablation

### 8.1 Frozen before any run

The platform already contains a forecasting stack, and two of its properties
are incompatible with evaluation unless pinned. Both are pinned here.

**Algorithm.** The primary model is `ProphetForecaster`
(`app/services/forecasting/prophet.py`), run **directly** — not through
`EnsembleForecaster` and not through the production fallback chain.

Reason, stated so it is not revisited later: `EnsembleForecaster` re-weights
its members by sample size on every `fit`, read from the code rather than its
docstring, which describes thresholds the code does not have:

```text
n < 10        lstm 0.0  prophet 0.6  linear 0.4   + Prophet fitted on
                                                    bootstrap-augmented data
10 ≤ n < 33   lstm 0.0  prophet 0.9  linear 0.1   (LSTM_MIN_ROWS = 33)
33 ≤ n < 50   lstm 0.2  prophet 0.8  linear 0.0
50 ≤ n < 100  lstm 0.4  prophet 0.6  linear 0.0
n ≥ 100       lstm 0.7  prophet 0.3  linear 0.0
```

The model's identity therefore changes as the series grows. An expanding window
over the h=7 entry condition of §7 crosses **three** of these thresholds
mid-evaluation, so "the model" would be a different composition in early folds
than in late ones — and the folds differ from each other precisely in how much
data they have. The comparison would confound model with sample size.

The `n < 10` branch is disqualifying on its own: it fits Prophet on
**synthetic data** produced by `_bootstrap_augment(…, target_size=30)`. A
metric computed on a model fitted to generated observations measures the
generator.

Weights are also redistributed silently when a member fails to fit
(`ensemble.py:107-123`), so two folds reporting "the ensemble" may not have run
the same combination — which is the same defect as the fallback chain below,
one level down.

**Fallbacks are recorded, never silent.** The stack documents a chain that
"never fails" (`app/services/forecasting/__init__.py:11`). For evaluation, any
fold or region where the primary model did not fit is reported as a fallback
with the reason, and is **not** counted as a result of the primary model.

**Features.** Primary: the target's own history only — no external regressor.

**Hyperparameters.** Library defaults, recorded verbatim in the version that
declares them. **No tuning, no search, no selection.** There is no validation
split for tuning because there is no tuning.

**Model selection rule:** none. The declared configuration runs. There is no
"best of" across configurations, because a best-of chosen on the test windows
is a result about the selection, not about the model.

### 8.2 GDP ablation

Secondary: target history + GDP growth (`NY.GDP.MKTP.KD.ZG`, `world_bank`
source only, as enforced in `app/services/forecasting/features.py`).

- **Vintage:** whichever mode §2 declares for the run — strict at the origin,
  or the identified fixed vintage under the pseudo label. The two are never
  mixed within one result.
- **Lag:** the value in effect at the origin under that mode. No forward fill
  past the origin, and no use of a value whose `fetched_at` exceeds it.

The GDP ablation **does not block the primary result.** Its verdict is one of
`helped` / `did not help` / `not testable`, decided by the primary metric under
the same folds.

`not testable` is the expected verdict for some time, and is a real answer
rather than a failure. GDP growth is annual: identifying its temporal
contribution needs origins spanning several annual changes. Within an 18-month
h=30 window that is roughly two updates — enough to distinguish regions, not
enough to identify a contribution over time.

### 8.3 Not in scope

**Air quality is not added**, and not for lack of interest. Adding it would
open a new temporal and geographic contract before any baseline exists, which
widens scope precisely where scope needs to be closed.

**The target is not changed** to make evaluation easier. The target defines the
question; changing it to fit available data answers a different question while
keeping the old name.

---

## 9. Amendment rule

This protocol may be amended at any time, **only as a new numbered version**,
registered with its date and its reason.

When an amendment is made after results exist under an earlier version:

- results computed under the earlier version are **retained and reported under
  that version**,
- they are **not recomputed** under the new one,
- the new version applies only to runs performed after it is registered.

A run in progress uses the version in force when it started.

The protection this provides is narrow and worth stating exactly: amending is
always permitted, but an amendment can never change what an already-computed
result says. The failure mode being guarded against is not a change of mind —
it is a change of mind that quietly rewrites history.

---

## 10. Status and next step

M2 is **not failed**. Its first result is stated plainly:

> **The dataset the platform currently holds does not permit an evidential
> backtest.** The target is a deterministic function of user input, has no
> entity key, is not append-only, and does not record any quantity that evolves
> over time.

M2 also cannot be closed as a completed benchmark until a valid temporal target
exists. Both statements hold at once.

The next task is separate from this protocol and is a prerequisite for it:
**define and begin collecting the canonical daily regional score of §1** —
#114. Until its first write, the clock in §7 has not started, and the region
set of §1.4 remains undeclared.
