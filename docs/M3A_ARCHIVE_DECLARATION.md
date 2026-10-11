# M3-A — forecast declaration on the archived series (preregistration)

    version              1.1
    declared             2026-10-11
    amended              2026-10-11 (1.1, before any run, after the co-architect review)
    vintage              Open-Meteo Historical Forecast API, downloaded 2026-10-11
    label                pseudo-real-time (M2 protocol §2.2), never a real-time claim
    relation to M3       a separate run; it neither replaces nor gates
                         docs/M3_FORECAST_DECLARATION.md
    amendable            only by a numbered version with a date and a reason

Everything below is fixed **before the evaluation has been run on this data
even once**. The commit that adds this file is pushed before the first run, and
the run records that commit. A rule written after a result is a choice of
result: this is the reason M2's §9 exists.

---

## 0. Why this exists

M3 forecasts a series that production collects live, hour by hour, and the
series has been lost twice with the servers that held it: about 2026-08-16 and
about 2026-10-09. Each loss restarts M3's clock, and its earliest evidential run
keeps moving into the next year.

The same quantity is published as an archive. Open-Meteo's Historical Forecast
API serves the hourly output of the same weather models that the live
`current` block reads from. Both series are complete for all 21 points from
2018-01-01 onward, while the live series begins 2026-10-10.

**Measured 2026-10-11: how close the archive is to what production recorded.**
The comparison set is the 5 216 hourly `current` readings per indicator that
were collected 2026-09-19..10-11. Archive values are read at the same
15-minute timestamps.

```
daily mean temperature, identical timestamps, 61 point-days
    median |difference|   0.09 C
    p90                   0.39 C
    max                   0.65 C
    mean bias            -0.02 C
single readings          median |difference| 0.2 C
ERA5 reanalysis (archive-api), same days     median 0.68 C -- a different source
```

The archive is therefore a reconstruction from a fixed vintage. It is close to
what was recorded, but **it is not the recording itself**. That is exactly the
case M2 §2.2 names: an answer to "how would the model score with today's data
arranged by period". It is weaker than M3, and this run is labelled as such.

## 1. What is forecast

The daily mean of hourly `temperature_2m`, one target, built by M3's §3 as
restated in its Amendment 1.3:

```
hour(point, h)    = the archive's hourly temperature_2m for UTC hour h
target(point,day) = mean(hour(point, h)) over the hours present in that UTC
                    calendar day, if at least 19 of the 24 are present;
                    otherwise the day is absent
```

Reports name the target `openmeteo-historical-forecast:temperature_2m`. That
name differs from M3's `openmeteo:temperature` on purpose, so that the two
are never read as one series.

## 2. Where it is forecast

The point set is `openmeteo-points-v1`, unchanged: the 21 capitals in
`REGION_CAPITALS` in `app/ingesters/openmeteo.py`. The archive is requested
at those coordinates. As in M3, a result describes 21 named points and may
not be described as regional.

## 3. The data

```
endpoint     https://historical-forecast-api.open-meteo.com/v1/forecast
parameters   latitude, longitude of each point; hourly=temperature_2m (and the
             nine other ingester variables, unused here); timezone=UTC;
             no model parameter, i.e. the API's default model selection
period       2018-01-01 .. 2026-10-10 inclusive, the last complete UTC day
             before the download
files        one gzipped JSON per point and year, kept with the owner's
             archive, with a SHA-256 manifest
```

**The vintage is pinned, not merely printed.** The manifest digest of the 189
weather files — SHA-256 over the sorted lines `<relative path>\t<sha256 of the
file>` — must equal

```
sha256:2d2ad4c1781102c810b8330adae0e6ad08dc99abed4ece91b21010b08a6b9f49
```

Any other value is a refusal before any model is fitted. A missing, added or
altered file changes it. The bytes that are hashed are the bytes that are
parsed: each file is read once.

A fresh download is a new vintage, because the provider may revise the
archive. A new vintage needs a new version of this declaration. Results from
two vintages are not compared as though they shared one.

**Every report also carries** (M2 §2.2):

- `snapshot_digest` of the rows it scored;
- the label;
- the commit SHA of this declaration: the last commit that changed this file;
- the commit SHA of the code that ran;
- the versions of Python, numpy, pandas, scikit-learn and xgboost.

The run refuses if either SHA cannot be determined, or if the working tree has
tracked modifications. An official result must name exactly what produced it.

**Nothing is mixed in.** Rows from production's live `openmeteo` ingester,
from ERA5, or from the copies recovered on 2026-10-11 are not part of this
series.

**The whole grid is checked, and anything short of it is refused, not filled.**
The expected grid is the 21 declared points × the 3 205 days from 2018-01-01 to
2026-10-10, which is 67 305 point-days. Every one of them must have at least 19
distinct UTC hours. Nothing is interpolated. The run refuses before fitting
anything, and reports what it found, if any of these holds:

- a point-day of the grid has fewer than 19 hours — a wholly absent day counts
  as zero hours. Checking only the dates that are present would miss an
  entire absent year: the co-architect review showed that a series missing all
  of 2018 passed such a check;
- the first or last day differs from the declared bounds;
- a point is missing, or a point is present that is not declared;
- a (point, hour) appears more than once — this archive is hourly, so a
  duplicate is a corrupt input, not something to average away;
- a value is not finite;
- a timestamp is not on the hour.

The refusal also keeps row positions equal to calendar days, which is what the
window arithmetic in `app/services/forecasting/evaluation.py` assumes.

Measured independently on 2026-10-11 by counting the hours in the files, before
any evaluation: all 67 305 point-days have all 24 hours.

## 3a. What the series is, and what a result does not show

The Historical Forecast API stitches together the first hours of successive
runs of numerical weather models. Without a `models` parameter, the provider
chooses the model per location, and models and their versions change over
time. Nothing here establishes that the series is homogeneous across nine
years. The comparison with production's recorded readings covers a few weeks
of 2026, not the history.

So a success under §9 means one thing only: forecasting this modelled series
beat the baselines on the declared windows. It does not mean:

- accuracy against thermometers;
- the skill of a numerical weather forecast at a 7- or 30-day lead time;
- that the result holds in climate regimes outside the evaluated period.

The download script and its log are kept with the archive as the request
record.

## 4. Horizons

7 and 30 days, as in M3. Each is reported separately.

## 5. Windows and training

M3's design: rolling origin, expanding window, non-overlapping test windows
with step equal to the horizon. What changes is **where the windows are placed
and how much history each is trained on**:

| horizon | windows | evaluation period |
|---:|---:|---|
| 7 | 104 | 2024-10-13 .. 2026-10-10 (728 days) |
| 30 | 24 | 2024-10-21 .. 2026-10-10 (720 days) |

- **Windows sit at the end of the series.** The code's default places them
  right after the minimum training slice, at the start. On nine years of data
  that would evaluate early 2018.
- **Each window trains on every day from 2018-01-01 up to its origin.** The
  shortest training slice is therefore 2 477 days. M3's
  `TRAINING_DAYS = {7: 90, 30: 180}` stay as floors, and are met by
  construction.
- **Two years of windows rather than M3's minimum of twelve.** Twelve windows
  at h=7 cover twelve weeks of one season. A verdict for a temperature target
  must span every season, twice.

## 6. Metrics

As M2 §3.

- **Primary:** macro MAE in °C. MAE is computed per point, then averaged with
  equal weight across the 21 points, per horizon.
- **Secondary:** RMSE, MASE (M2 §3.2) and bias, with the same aggregation.
- **Intervals and the success test:** §9.

## 7. Baselines — fixed here, before any model is run

1. `Naive` — the last value carried forward.
2. `SeasonalNaive`, `season_length = 7`.
3. `MovingAverage`, `window = 7`.
4. **`Climatology`** — new in this declaration, because nine years of history
   make it both possible and obligatory.

   The forecast for day *d* is the mean of the target over every calendar
   day within ±3 days of *d*'s month-day, in every earlier year whose
   matching days all fall on or before the origin. 29 February is matched as
   28 February.

   **Which years count.** An earlier year qualifies if all seven of its window
   dates fall on or before the origin **and** at least one of them is
   observed. Its observed days enter one pooled mean together with those of
   every other qualifying year.

   At least two qualifying years are required. Where there are fewer, that
   point-window is not scored for this baseline, and the count is reported.
   With complete history from 2018 and windows from 2024, no point-window has
   fewer than six.

   A temperature model that cannot beat the average of the same week in
   previous years has learned the calendar, not the weather. At h=30 this is
   expected to be the baseline to beat.

## 8. Candidates — frozen

Exactly two candidates, named in the code as an explicit list rather than
taken from whatever `CANDIDATES` holds:

- `linear_trend`;
- `xgboost_lag`.

Each is used with its constructor parameters as they stand in
`app/services/forecasting/challengers.py` at the code commit the run records.
`lstm` has role `shadow`: if it is run, it is reported separately and never
counted.

Adding a candidate requires a numbered version with a date and a reason.
**Every candidate ever run under this declaration is reported.** None is
dropped because it lost.

## 9. What counts as success

**The family of claims.** 2 candidates × 2 horizons = 4 possible claims of
success. The family-wise error rate is held at α = 0.05 by Bonferroni, so each
claim is tested one-sided at α/4 = 0.0125.

**A claim (candidate c, horizon h) succeeds only if c beats every one of the
four baselines.** No "strongest baseline" is picked from the results. For each
baseline b:

- the unit is the window: d_w = macro MAE of c in window w − macro MAE of b in
  window w. The 21 points stay together inside a window;
- the statistic is the mean of d_w over the windows;
- its upper bound is the 98.75th percentile of a **circular block bootstrap**
  over the time-ordered windows (one-sided 1 − 0.0125). That bound must be
  below zero.

Requiring every baseline is an intersection–union test, so it needs no further
correction across baselines. It implies that c's macro MAE is lower than every
baseline's. A tie is not lower, so ties lose.

**The bootstrap, fixed now and never tuned:**

- block length L = ⌈n^(1/3)⌉, where n is the number of windows:
  - h=7: n = 104, L = 5;
  - h=30: n = 24, L = 3;
- blocks of L consecutive windows wrap around the end of the sequence;
- each resample draws ⌈n / L⌉ block starts uniformly and truncates to n
  windows;
- 10 000 resamples, `numpy.random.default_rng(0)`;
- one set of resampled index sequences per horizon is used for every
  candidate–baseline comparison of that horizon.

Non-overlapping windows do not make neighbouring weeks independent. Weather,
season and the shared expanding training history connect them, and the blocks
keep that dependence in the resamples. A block length chosen because it
produced a success would void the test, which is why the rule is written here.

**Pairing is checked by dates, not by counts.** Every (model, point) report
must hold exactly n windows with the same `train_end`, `test_start` and
`test_end` as every other, and every per-window MAE must be finite. Otherwise
the claim is "not_comparable" — never a bootstrap over a shorter or shifted
sample.

**Power.** At h=30 there are 24 windows in 8 blocks. A claim that fails there
is weak evidence that no effect exists, and is reported in those words.

Otherwise a claim has not succeeded, whatever its secondary metrics show. All
four claims are reported. No best combination is picked afterwards.

## 10. How results may be described

- Every table, figure and summary carries **"pseudo-real-time · archive vintage
  2026-10-11 · not a real-time claim"**.
- Per M2 §2.2, a result may not be called backtesting, historical performance
  or real-time accuracy.
- It is not an ESG forecast (M2 §10.3) and not regional (§2 above).
- It does not stand in for M3. M3's live collection continues and remains the
  only route to a real-time claim. How M3's clock is treated after the second
  loss is a separate amendment to that file, not decided here.

## 11. What this declaration does not do

- It writes nothing to production and loads nothing into any production table.
- It does not alter M3's target, point set, gate constants or dates.
- It does not cover air quality. The archive for that was downloaded too, and
  needs its own declaration under M3's one-target rule.

## 12. Amendment record

| version | date | reason |
|---|---|---|
| 1.0 | 2026-10-11 | Declared before any run on the archive, after the second loss of the live series. |
| 1.1 | 2026-10-11 | Revised **before any run**, after the co-architect review (GPT): the whole 67 305-point-day grid is checked (a missing year passed the old check); the archive digest is pinned; the declaration and code SHAs and library versions are recorded, and a dirty tree is refused; the candidates are an explicit list; success is a family-wise, intersection–union test against every baseline with a circular block bootstrap; windows are paired by dates; the limits of a modelled series are stated (§3a). |
