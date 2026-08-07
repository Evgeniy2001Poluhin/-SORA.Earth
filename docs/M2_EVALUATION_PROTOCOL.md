# M2 Forecasting — evaluation protocol (pre-registration)

```
Protocol            preregistered, version 1.0
Target readiness    FAILED
Reason              canonical temporal unit not established;
                    insufficient non-overlapping origins
Model runs          none performed
```

**Version:** 1.0 · **Registered:** 2026-08-08 · **Supersedes:** nothing
**Evidence commit:** `1b0aacc` (production), schema `c58e21a9f7d4`
**Audit source:** #75

This document is written **before** any model is run, and before the data it
describes exists. That is the point. A protocol written after seeing results
cannot be distinguished from a description of them.

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

```
region_id            declared set, fixed in advance (§1.4)
observed_date        the day the state refers to
known_at             when the value became knowable  (= write time)
input_snapshot_id    identity of the inputs used
inputs               the inputs themselves, in full
total_score          the value
calculation_version  which scoring code produced it
```

Two requirements that the audit showed are not decorative:

- **`inputs` must be stored, not only referenced.** Today part of the actual
  input to the score is discarded (§6.4), so a stored row cannot be
  reproduced from what is stored beside it. An `input_snapshot_id` pointing at
  nothing retained does not fix this.
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

The declared region set must be fixed before the first snapshot is written and
recorded in this document as a version bump. It cannot be chosen from whichever
regions turn out to have data — that is selection on the outcome.

The current region field is derived from user input with a default, which is
why it must be replaced rather than inherited: today `Europe` holds 167 rows
across 14 days and `North America` holds 6 rows across 4 days.

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

The current fixed vintage, truncated by `observed_period`. This answers "how
would the model score with today's data arranged by period", which is a
different and weaker question.

Any result from this mode carries the label in every table, figure and summary
that reports it. It may not be described as backtesting, historical
performance, or real-time accuracy.

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

- **RMSE**, same aggregation — reported alongside, never instead.
- **MASE** against the naive baseline of §4.

### 3.3 Prohibited

**MAPE is not a primary metric and is not a tie-breaker.** It is asymmetric,
undefined at zero, and rewards under-prediction. It may appear only as a
descriptive figure, clearly marked as such.

---

## 4. Baselines

Both are fixed here, before any model exists:

1. **Last value** — carry the most recent observed region value forward.
2. **Trailing mean** over a window declared in this document before the first
   run. The window is not tuned. If it turns out to be a poor choice, that is a
   result about the baseline, not a licence to change it.

A model that does not beat both baselines on the primary metric has not
succeeded, whatever its secondary metrics show.

---

## 5. Folds

- **Expanding window**, rolling origin.
- **Test windows must not overlap.** Overlapping windows re-use observations
  and inflate apparent stability.
- Origins are defined by **calendar periods**, not by a fixed number of rows.
  The series is irregular, and row-counted origins would place origins at
  different real-time spacings depending on activity.
- Horizons: **h = 7** and **h = 30** days, reported separately and never pooled.
- Seeds fixed and recorded. Any stochastic component reports across seeds.

**Missing data policy:** declared before the first run. A region-day with no
observation is a miss, not a zero and not an interpolation. Coverage per window
is reported next to the metric it produced.

---

## 6. The audit that put this protocol in a failed state

Read-only, on production `1b0aacc`, 2026-08-08. Nothing was run or changed.

### 6.1 The series as it stands

```
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

```
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

```
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

```
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

```
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
| initial training window | ≥ 90 calendar days | ≥ 180 calendar days |
| non-overlapping test windows | ≥ 12 | ≥ 12 |
| expected region-day coverage in each window | ≥ 80% | ≥ 80% |
| regions with the full set of folds | all declared | all declared |
| weighting | one weight per region-day | one weight per region-day |

Twelve is not a mathematical guarantee. It is a minimum operational gate chosen
in advance, sufficient to estimate the spread of loss across origins. Five
30-day folds were considered and rejected as too fragile to support a verdict.

**What this costs in calendar time**, from the first snapshot write:

```
h=7     90 + 12 × 7  =  174 days  ≈   6 months
h=30   180 + 12 × 30 =  540 days  ≈  18 months
```

h=7 is the near-term objective. h=30 is declared and remains declared, but it
is a 2028 horizon and should be planned as one.

---

## 8. Model and ablation

- **Primary:** target-only model against the two baselines of §4.
- **Secondary:** target-only + GDP growth, as ablation.

The GDP ablation **does not block the primary result.** Its verdict is one of
`helped` / `did not help` / `not testable`, decided by the primary metric under
the same folds.

`not testable` is the expected verdict for some time, and is a real answer
rather than a failure. GDP growth is annual: identifying its temporal
contribution needs origins spanning several annual changes. Within an 18-month
h=30 window that is roughly two updates — enough to distinguish regions, not
enough to identify a contribution over time.

**Air quality is not added**, and not for lack of interest. Adding it would
open a new temporal and geographic contract before any baseline exists, which
widens scope precisely where scope needs to be closed.

**The target is not changed** to make evaluation easier. The target defines the
question; changing it to fit available data answers a different question while
keeping the old name.

---

## 9. Amendment rule

This protocol may be changed **only as a new numbered version**, and only
before results computed under the current version are seen — never after.

If a change is made after any result exists:

- the new version is registered with its date and its reason,
- results computed under the previous version are **retained and reported
  under that version**,
- they are **not recomputed** under the new one.

A protocol that can be edited after seeing results provides no protection, and
carries the appearance of protection, which is worse than none.

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
**define and begin collecting the canonical daily regional score of §1.** Until
its first write, the clock in §7 has not started.
