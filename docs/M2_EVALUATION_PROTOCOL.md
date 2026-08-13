# M2 Forecasting — evaluation protocol (pre-registration)

```text
Protocol            preregistered, version 1.1 (amended 2026-08-13 under §9)
Target readiness    FAILED
M2 outcome          CLOSED -- NEGATIVE RESULT: no qualifying temporal target
Reason              every candidate the platform holds is either subject-level
                    and constant, or time-varying and not subject-level. No
                    source is both, so an evidential 7/30-day backtest is
                    impossible regardless of how many records accumulate
Benchmark executed  no
Engineering failure no
Protocol failure    prevented -- see §7.1 and §10.4
M3 eligibility      allowed under the boundary in §10.6
```

**This is a preregistered negative feasibility result.** M2 is not a failed
benchmark and must not be recorded as a completed one: no benchmark was run,
because the target does not admit one. The protocol below is retained in full
for a future target that does.

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

**Uniqueness:** a database constraint on
`(region_id, observed_date, calculation_version)`. Without it a retried
scheduler run produces two records for one region-day, and the per-region-day
weighting of §3.1 silently double-counts it.

`calculation_version` is part of the key rather than a plain column so that a
recomputation under new scoring code can be **retained alongside** the original
instead of replacing it. Keying on `(region_id, observed_date)` alone would
make the two mutually exclusive, and the whole point of the append-only rule is
that the earlier value survives.

**Exactly one version is canonical for evaluation.** The protocol version in
force names it, and every fold reads that version only. Without this, a
recomputation would silently change results already reported — the same
failure §9 forbids by another route.

**Retries are idempotent only when the content matches.** The key
`(region_id, observed_date, calculation_version)` does not contain
`input_snapshot_id`, `inputs` or `total_score`, so "a record already exists"
does not mean "the same record". A re-run must compare the immutable snapshot
fields:

- **identical** — a no-op, counted as a retry, not an error. This is the
  scheduler re-running after a transient failure.
- **different** — a **conflict**. Refused and reported, never silently
  accepted. A differing value under an unchanged `calculation_version` means
  either the inputs moved without the code moving, or two writers disagree;
  both are findings, and swallowing them reintroduces the overwrite this
  contract exists to prevent.

A scheduler retry and a backfill are distinguished by the key, not by
intent: a retry carries the same `calculation_version`, a backfill a new one.
Nothing needs to know which caller it was.

**Backfill:** writing a region-day that already exists under the same
`calculation_version` is refused — it cannot be distinguished from a duplicate.
A new `calculation_version` may write, and both records persist; the original
is never overwritten.

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

**Declared 2026-08-13 as `ru-regions-v1`: the 85 Russian federal subjects
enumerated in `app/services/esg_aggregator.DECLARED_REGIONS_V1`.**

What made this decidable was a measurement, not a preference. The observation
layer holds 104 distinct ids, and that is not 85 regions with 19 missing. It is
two populations of different kinds:

```
canonical    85 Russian federal subjects, RU-*
contextual   21 openmeteo entities, 19 of them countries
             (BRA CAN CHN DEU ESP FRA GBR IDN IND ITA JPN KEN KOR MEX
              NGA NLD POL USA ZAF)
overlap      2 -- RU-MOW and RU-SPE
```

Both canonical sources cover the declared set exactly -- no extras, none
missing -- and a test asserts it, so a divergence becomes someone's decision
rather than a silent change in what "85 regions" means.

The 19 countries are **not excluded regions**. They are a different unit of
observation, and §10 already names forecasting them as a legitimate separate
product. Their data stays in `environmental_observations`.

The refusal therefore sits on the **canonical writer**, not on persistence.
`require_declared()` raises `UndeclaredRegionError`; a guard in `persist.py`
would delete the contextual population, which is data the protocol allows.

The set was fixed by this version bump, which is required **before the first
snapshot is written** — not before the first model run, because the coverage
denominators of §5 and §7 are meaningless without it.

A concrete precedent now exists and should be followed rather than reinvented:
`app/services/esg_aggregator.DECLARED_REGIONS` enumerates 85 `region_id`
values explicitly, with a test asserting the set still matches both ingesters.
That is the shape this requires — an enumeration plus a drift guard, not a
count.

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

1. Let `S` be the first `observed_date` in the canonical series and **`E` the
   last `observed_date`** in it. `E` is the end of the most recent test window,
   not an origin — every day up to and including `E` is available to be
   predicted, so none is discarded.
2. Windows are laid **backwards** from `E` in contiguous blocks of `h` days:
   window *k* covers `[E − k·h + 1, E − (k−1)·h]` for `k = 1, 2, …`, so window
   1 is `[E − h + 1, E]`. Blocks are contiguous and therefore non-overlapping
   by construction.
3. The origin of window *k* is `E − k·h`, the day before its window starts.
   Training uses every observed region-day at or before that origin. The
   initial training requirement of §7 applies to the **earliest** window used:
   it is admitted only if its origin is at least 90 days (h=7) or 180 days
   (h=30) after `S`.
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

These windows are laid **forwards from `S`**, because the question here is
descriptive — how much this series could support at best. The prescriptive
construction of §5.1 lays them **backwards from `E`**, and the two do not
coincide. Neither is used for the other's purpose, and the counts below are not
an application of §5.1.

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

### 7.1 Movement — added in v1.1

| condition | h=7 | h=30 |
|---|---:|---:|
| the target takes a different value in ≥2 distinct **source periods**, per declared region, in **every** window | required | required |

Every condition in the table above counts records. §10.2 recorded what that
leaves open: twelve non-overlapping windows at ≥80% coverage is a test of record
count, and repeated writes of a constant pass it. Measured at the time, 1.00
distinct values per region across eight days.

Four things that look like new information and are not, and none of which may
be counted as movement:

- **a repeated write of the same period and value** — the upsert of #121, whose
  point is that an unchanged snapshot produces no new observation
- **a different `ingested_at`** — when the row was written, which is the
  conflation #121 removed
- **a new `source_revision` carrying the same value** — the content was
  restated, not changed
- **an interpolated point** — #132: resampling fills every calendar day, so a
  date being present stops meaning something was observed

No threshold on magnitude is set, deliberately: a minimum size of change would
be a tuned parameter smuggled into a pre-registration. The condition is that the
value differs between two source periods inside the window, nothing more.

The consequence is a real constraint rather than a technicality, and it is the
finding of §10.3 in a form the gate can check: **a target cannot be evaluated at
a horizon shorter than its own cadence.** A quantity published quarterly does
not move inside a 7-day window.

`app/services/forecasting/entry_conditions.py` implements all seven conditions;
`tests/test_m2_entry_gate.py` holds them, including each of the four
non-movements above.

**Amendment record (§9).** Registered 2026-08-13, reason: the v1.0 gate could be
satisfied by a series containing no observation, which §10.2 measured rather
than supposed. No result existed under v1.0 — no model has been run — so nothing
is recomputed and nothing is rewritten. The amendment tightens the gate; it
cannot admit anything v1.0 refused.

---

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

**A method-specific failure may not shrink that method's population.** A
region-fold dropped because the primary model failed to fit is dropped from the
baselines too, for that metric. Otherwise the comparison deciding the verdict
would run over two different populations, and a model that fails precisely on
its hard cases would look better for having failed.

Stated once, as the general rule: **for any given metric, the set of
region-folds it is computed over is identical across every method being
compared.** A region-fold leaves a metric only for reasons that hold for all
methods alike — and when it does, it leaves for all of them.

The distinction matters, because not every exclusion in this document is
method-specific and they must not be swept together:

| exclusion | applies to | effect |
|---|---|---|
| §3.2 zero MASE denominator | all methods equally — the scaling constant is a property of the data | leaves **MASE only**; MAE and RMSE are still computed there, for every method |
| §4 no observation at or before the origin | all methods — nothing can be fitted or carried forward | leaves every metric |
| §5.3 no observed test day | all methods — there is nothing to score against | leaves every metric |
| §8.1 primary model failed to fit | **one method only** | leaves every metric, for every method, so the comparison stays like-for-like |

Only the last is an asymmetry being corrected. The first is deliberately *not*
a whole-population exclusion: MASE being incomputable says nothing about
whether MAE is.

Exclusions are counted and reported per metric. **If more than 10% of
region-folds leave the primary metric for any reason, the run's verdict is
`not testable`** rather than a comparison over what survived. That threshold is
fixed here, before it is known which method it would favour.

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

## 8a. What the target is made of, and why air quality is not in it

Decided 2026-08-13, closing #117. Recorded here rather than left in a code
comment because the composition of the score is the thing a benchmark is a
benchmark *of*: changing it silently later would invalidate every result
measured before the change, and nobody would be able to tell which side of the
change a number came from.

**Air quality is not a component of the canonical target.**

Two independent reasons, and the second is the one that settles it.

**The measured source produces nothing.** `openaq` has written zero rows in
every one of its 62 recorded runs, and was stood down from scheduling in #124
because the stations reachable from this deployment publish nothing current.
The formula read `openaq:pm25_ugm3`, so that term has been `None` for every
score the platform has ever produced.

**The available substitute does not cover the same regions.**
`openmeteo_air_quality` is live and current, and it is *not* wired in. It covers
21 regions, of which exactly two -- RU-MOW and RU-SPE -- appear in the 85 this
index is defined over. Including it would make `env_score` mean a measured air
quality for two regions and a baseline index for the other eighty-three, under
one column name. That is not a partial improvement; it is two different
quantities sharing a column, and no consumer could tell which one it received.

It is also modelled rather than instrumental -- CAMS reanalysis, not a station
reading -- so substituting it for the measured source would change what the
component *is* as well as which regions have it.

### The rule this fixes

* the canonical target does **not** include air quality;
* `openmeteo_air_quality` remains a modelled contextual signal and must not be
  substituted for a measured one;
* `openaq` returns only after a freshness canary defined **in advance** -- a
  stated number of current rows across a stated set of regions, agreed before
  the data is looked at, so the decision to re-include cannot be made by
  inspecting whichever window happens to look best;
* the component list is stated explicitly and versioned with
  `calculation_version`; a change to it is an amendment under §9, not an edit.

### What this does not decide

Whether air quality *should* eventually be in the target. It should, if a source
covering the declared regions with instrumental measurements becomes available.
This records that today no such source exists, and that filling the gap with a
different quantity is worse than leaving it visible.

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

M2 is **not a failed model**. No model was run. The target contains no temporal
signal, which is a finding about the data and not a result about any method.

Its first result is stated plainly:

> **The dataset the platform currently holds does not permit an evidential
> backtest.** The evaluation target audited in §6 is a deterministic function of
> user input, has no entity key, is not append-only, and records no quantity
> that evolves over time. The regional index examined in §10.2 fails the same
> test for a different reason: its inputs are static snapshots, so it is
> constant within every region.

M2 cannot be closed as a completed benchmark. §10.3 records the verdict and the
state it moves to.

### 10.1 The observation layer already exists

Found after the audit above and material to §7, so recorded here rather than
left in the issue.

`environmental_observations` is live and ingesting hourly:

```text
event_time span    2026-07-31 … 2026-08-07   (8 days, all present)
regions            104
indicators         22
sources            4
rows               53,006, all is_valid
```

It carries part of the §1.1 contract, and the two overstatements in the
first version of this section are worth correcting rather than quietly
editing, because both were guarantees read off a schema instead of measured.

**Three distinct timestamps** — `event_time`, `published_at`, `ingested_at`.
This holds, and it is the property that matters most.

**`source_revision` is declared and never written.** Measured on production:
NULL in **all 57,884 rows**, every source. The column that would hold a
vintage identifier is empty, which is why §2.2 requires the vintage to be
recorded separately and #121 has to add one.

**Deduplication is conditional, not universal.** The index is
`UNIQUE (source, source_record_id) WHERE source_record_id IS NOT NULL`, over a
nullable column. Rows sharing a source with a NULL id are all permitted. Today
no row has a NULL id — 0 of 57,884 — so the guarantee holds as a property of
the data that has arrived, not of the schema that admits it. Before any strict
reconstruction depends on it, either the column becomes NOT NULL for score
inputs or a deterministic fallback key is defined; until then it is an open
gate, recorded here as one.

**What is missing is the score layer, not the observations.**
`region_esg_scores` carries `UNIQUE (region_code)` — one row per region, ever,
upserted in place, so each recomputation destroys the previous value. Its 85
rows all read `updated_at = 2026-07-31 20:04:26`, and the aggregator that
writes them reads `region_signals`, which has taken no row since 2026-07-30
(#116).

### 10.2 The §7 clock has not started, and will not start by waiting

An earlier revision of this section concluded that a dated score series could
be reconstructed back to 2026-07-31, and that the §7 clock had therefore
started. **That was wrong, and the correction is the decisive result of this
document.**

The observation layer is real. The values inside it are not observations.

**Audit window: `event_time` from 2026-07-31 to 2026-08-07 inclusive**, the
eight days in §10.1. Every figure below is computed over exactly that window,
including the denominator of each average, so the table is reproducible from
the window alone.

Distinct values **per region**, averaged across the regions carrying the
metric:

```text
source                 indicator            days  regions   distinct/region
rosstat                avg_income_rub          8       85              1.00
rosstat                budget_transparency     8       85              1.00
rosstat                digital_gov_index       8       85              1.00
rosstat                life_expectancy         8       85              1.00
rosstat                unemployment_rate       8       85              1.00
sber_veb_baseline      esg_index_baseline      8       85              1.00

openmeteo              temperature             8       21             95.81
openmeteo              humidity                8       21             52.28
openmeteo              wind_direction          8       21            112.19
openmeteo_air_quality  pm2_5                   3       21             46.38
openmeteo_air_quality  ozone                   3       21             44.38
```

An earlier revision of this section quoted figures from a nine-day window while
§10.1 stated eight, which made the numbers unreproducible from the stated
window even though the conclusion was unaffected. The values differ slightly
from that revision for the same reason -- a day more or less of weather changes
a count of distinct temperatures, and changes nothing about a constant.

Every metric the ESG formula reads has **exactly one value per region**, on
every day. Not slow-moving annual statistics — static literals in the source
tree. `app/ingesters/sber_veb_baseline.py` is a hardcoded dict of 85 constants
with no network call; `app/ingesters/rosstat.py` makes no network call either
and imports `data.rosstat_snapshot_2024`, described in its own docstring as an
offline 2024 snapshot refreshed roughly half-yearly. Both re-emit their values
each run stamped with `now`, which is why `event_time` advances while the
numbers do not (#121).

Values differ *between* regions — 34 to 77 distinct values across the 85 — so
the score is meaningful as a cross-section. It has no temporal content at all.

**Consequences, and none of them are lifted by time:**

- A canonical daily score built exactly as §1 specifies would be, for each
  region, a **constant**. Against it the last-value baseline of §4 is exact:
  MAE = 0. No model can beat it, and §8.2's GDP ablation is unanswerable.
- The §7 entry conditions would be **satisfied on schedule by a series
  containing nothing**. Twelve non-overlapping windows at ≥80% coverage is a
  test of record count, and repeated writes of a constant pass it. This is the
  one way the gate as written can be met without the thing it was gating for.
- Therefore #114 **is not to be implemented in the form proposed there.** A
  scheduled writer would produce a table satisfying the schema and the count,
  holding no new observation. The issue anticipated this scenario as a risk;
  it is the measured state.

### 10.3 Verdict

> **M2 forecasting feasibility: target readiness FAILED.** The current regional
> ESG score is a structural index computed from versioned static snapshots. An
> evidential 7/30-day backtest is impossible regardless of how many repeated
> records accumulate.

M2 moves to **`BLOCKED — no temporal target`**. Not closed as a completed
benchmark: nothing was benchmarked, and recording it as finished would assert a
measurement that was never taken.

This protocol is retained unchanged for a future target. Every fixed choice in
it — metrics, baselines, folds, modes, entry gate, amendment rule — was made
before any data existed and remains binding on the first run that ever happens.

**What was decided, and what was ruled out:**

- **Adopted:** the ESG score is a structural cross-section and is not
  forecast. That is the honest current product. The **85 regions are the
  current score-table population, not the §1.4 declared set** -- §1.4 remains
  undeclared, and nothing here may be used as protocol coverage until it names
  the exact ids and a mapping version.
- **Separate product, not M2:** forecasting `openmeteo` and air quality, which
  do vary, over their 21 regions. Legitimate, and it may not be renamed an ESG
  forecast.
- **Future project, not now:** acquiring inputs that genuinely change on a
  monthly or quarterly cadence for a declared region set, storing their source
  period and vintage rather than the ingester's run time (#121). Only when a
  varying target exists does the §7 clock start.

M3 does not begin before that.

§1.4 is declared as of 2026-08-13 (`ru-regions-v1`, 85 subjects): the 104 ids
in the observation layer proved to be two populations rather than one
incomplete set, so the choice was between kinds and not between members. The
canonical score of §1 still does not exist -- declaring the population is a
precondition for building it, not the building.

### 10.4 Every candidate, measured — added in v1.1

§10.2 measured the score. This measures **everything the platform holds**, so
the negative result rests on an exhausted set rather than on the one target that
was audited first. Read from production 2026-08-13, legacy rows excluded (their
`event_time` is a stamped ingestion time, removed by #121).

| candidate | granularity | of the 85 declared | distinct source periods | distinct values per region | moves |
|---|---|---:|---:|---:|:--:|
| `rosstat` × 5 metrics | federal subject | **85** | 1 | **1.00** | no |
| `sber_veb_baseline` | federal subject | **85** | 0 | **1.00** | no |
| `openmeteo` × 10 metrics | city | **2** | 14 | 60 – 165 | yes |
| `openmeteo_air_quality` × 6 | city | **2** | 9 | 76 – 143 | yes |
| `country_indicator_history` (World Bank) × 7 | **country** | **0** | 66 for RUS | 179 for RUS | yes |
| `region_esg_scores` (the §1 target) | federal subject | 85 | — | upserted in place, one `updated_at` | no |

**The two properties are mutually exclusive across the whole set.** Everything
that covers the declared region set is constant; everything that moves covers
two of eighty-five, or none at all. Not one candidate is both subject-level and
time-varying.

The gap is not marginal. §7 requires **all** declared regions in every window;
the varying sources reach 2 of 85, short by a factor of 42. No accumulation of
history closes it, because it is a property of what the sources publish and not
of how long they have been publishing it.

Each of the seven measurements the closure requires:

- **Regions** — coverage of `ru-regions-v1`: 85 for the constant sources, 2 for
  the varying ones, 0 for the country series.
- **Periods** — distinct source periods: 1 for rosstat (the 2024 annual
  snapshot), 0 for sber (`not_applicable`, no time dimension at all).
- **Values** — distinct target values per region: exactly 1.00 for every
  subject-level candidate.
- **Movement** — inter-period variation: none, for every subject-level
  candidate.
- **Vintage** — source time rather than load time: available since #121
  (`period_start`/`period_end`, `source_revision`), and measured off that axis
  here.
- **Provenance** — revision and source: present, and deliberately *not* counted
  as movement (§7.1).
- **Artificial points** — interpolation excluded (#132), and excluded again by
  the gate.

### 10.5 Closure — added in v1.1

```text
M2 outcome:            CLOSED — NEGATIVE RESULT
Reason:                no qualifying temporal target
Benchmark executed:    no
Engineering failure:   no
Protocol failure:      prevented
M3 eligibility:        allowed under the boundary in §10.6
```

**The commit this verdict is attached to.** A closure that names no revision is
a claim about a tree nobody can identify later.

```text
merged           e82d682d2ee102374680e93bf201c6693c88f1ac  (PR #158)
protocol         v1.1, amended 2026-08-13 under §9
push CI          7 of 7 jobs green: backend-tests, shell-scripts,
                 environmental-postgres-tests, frontend-checks,
                 migration-bootstrap, integration-tests, required-checks
secret scan      green
preceded by      91cf5f9 (PR #157, the run-record decision fields)
```

The §7 gate is executable at that revision --
`app/services/forecasting/entry_conditions.py` -- so the condition this closure
turns on can be re-run rather than re-read.

**What was checked.** Every source in the observation layer, the country
indicator series, and the score table itself — §10.4. The audit of the
`Evaluation` target — §6. The score's temporal content over the eight days it
existed — §10.2. The entry gate's own soundness — §7.1.

**Why the existing data cannot answer the question.** A forecast is a claim
about a quantity that changes. Every subject-level series the platform holds
takes exactly one value per region: rosstat publishes an annual 2024 snapshot,
sber a static baseline with no time dimension. The series that do change are
weather and air quality at two city coordinates, and country aggregates. There
is no overlap.

**Why MAE = 0 is not a success.** Against a constant, the §4 last-value baseline
carries the previous value forward and is exact. Every model that also carries
the value forward ties it, and nothing can beat it. A results table reading
`MAE = 0` would record that the target does not move — a fact about the data,
presented in the shape of a fact about a method. That is the specific misreading
this closure exists to prevent.

**What would unblock a re-attempt.** A source that is all of: genuinely varying
between its own publication periods; published at least monthly or quarterly;
covering the 85 ids of `ru-regions-v1`; carrying its own source periods and
vintage rather than an ingestion timestamp; and not a re-publication of a static
snapshot. The §7 clock starts at the first observation of such a target — not
before — and runs 90 days plus twelve windows for h=7.

**What remains separately open.** #84 (`environmental_observations` has no
reader for a score layer) and #75 (point-in-time provenance, against leakage)
are prerequisites for a strict real-time backtest under §2.1. Neither lifts this
blocker, and neither is a reason to hold M2 open: they are conditions on a
future run, not on this verdict.

### 10.6 The boundary M3 inherits — added in v1.1

> **M3 does not claim that M2 demonstrated a forecastable temporal ESG target.
> M2 is closed with a negative result, because no such target exists in the data
> available.**

M3 may begin. What it may not do is cite M2 as validation of a model: nothing
was benchmarked, and the one number a benchmark would have produced —
`MAE = 0` against a constant — is a property of the data.

The prohibition in §10.3 stands unchanged: forecasting `openmeteo` and air
quality over their 21 entities is a legitimate separate product, and it may not
be renamed an ESG forecast.
