# M3-A — forecast declaration on the archived series (preregistration)

    version              1.0
    declared             2026-10-11
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

**Vintage identification** follows M2 §2.2. Every report carries:

- the manifest digest of the files;
- `snapshot_digest` of the rows it scored;
- the commit SHA of the code that ran;
- the label.

A fresh download is a new vintage: the provider may revise the archive.
Results from two vintages are not compared as though they shared one.

**Nothing is mixed in.** Rows from production's live `openmeteo` ingester,
from ERA5, or from the copies recovered on 2026-10-11 are not part of this
series.

**Missing days are refused, not filled.** If any point-day in the period falls
below the 19-hour floor, the run refuses and reports the count. Nothing is
interpolated. Measured for 2026-04-01..10-10: 4 053 of 4 053 point-days meet
the floor. The run checks the whole period. The refusal also keeps row
positions equal to calendar days, which is what the window arithmetic in
`app/services/forecasting/evaluation.py` assumes.

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
- **Intervals:** bootstrap, seeded (`seed=0`), as the existing report does.

## 7. Baselines — fixed here, before any model is run

1. `Naive` — the last value carried forward.
2. `SeasonalNaive`, `season_length = 7`.
3. `MovingAverage`, `window = 7`.
4. **`Climatology`** — new in this declaration, because nine years of history
   make it both possible and obligatory.

   The forecast for day *d* is the mean of the target over every calendar
   day within ±3 days of *d*'s month-day, in every earlier year whose
   matching days all fall on or before the origin. 29 February is matched as
   28 February. At least two earlier years are required. Where there are fewer,
   that point-window is not scored for this baseline, and the count is
   reported. With history from 2018 and windows from 2024, no point-window
   has fewer than six.

   A temperature model that cannot beat the average of the same week in
   previous years has learned the calendar, not the weather. At h=30 this is
   expected to be the baseline to beat.

## 8. Candidates — frozen

`linear_trend` and `xgboost_lag`, as defined in
`app/services/forecasting/challengers.py` at the commit that adds this file.
`lstm` has role `shadow`: if it is run, it is reported separately and never
counted.

Adding a candidate requires a numbered version with a date and a reason.
**Every candidate ever run under this declaration is reported.** None is
dropped because it lost.

## 9. What counts as success

Per horizon, a candidate succeeds only if both hold:

- its macro MAE is lower than every baseline's macro MAE (ties lose);
- the 95% paired bootstrap interval of `candidate − strongest baseline` lies
  entirely below zero.

Otherwise it has not succeeded, whatever its secondary metrics show. Each
candidate's verdict is reported for both horizons. No best combination is
picked afterwards.

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
