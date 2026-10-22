# P1 cache feasibility: calendar, budget and three forecast-only probes

Local planning, 22 October 2026. No real-data calibration or quality scoring.
Planner: `scripts/plan_forecast_replay_cache.py`; output is deterministic JSON.
The emitted plan is not an acquisition worker and does not start a download.

## Source and population

[Single Runs documentation](https://open-meteo.com/en/docs/single-runs-api)
advertises IFS HRES from March2024; it does not guarantee each requested hour.
Use the full daily issue calendar **2024-04-01 through 2025-11-28**, after the
advertised start month. All21 `OPENMETEO_POINTS_V1` points, IFS00UTC on issue
date,8 forecast days (skip issue day), Celsius/UTC, same settings as frozen P0.
Do not substitute Previous Runs' mixed initializations or a different model.

There are **607** calendar runs. **11** are candidate reuses from P0 (manifest
pin in planner); reuse is permitted only after checking exact URLs, file hashes
and whole point/hour grids. Thus **596** new runs if reuse passes,607 otherwise.
ERA5 verification can be reused only from that same pinned P0 snapshot/vintage;
do not redownload newer labels and silently treat them as the same experiment.

Potential mature pairs per point/lead, before checking provider data:
first origin has273,272,271,270,269,268,267 for leads1..7. Last origin has
364,363,362,361,360,359,358. All exceed180 **structurally**. This is not a
measurement of valid downloaded pairs: missing files/hours may change readiness.
No point is dropped if a later cache check finds gaps.

First acquisition slice: **180** consecutive issues,2024-06-27..2024-12-23,
eligible for all leads at first evaluation origin2024-12-31T23:59:59UTC.
It is a cache feasibility slice, not completion of the12-window P1 experiment.
Select these dates from calendar/maturity only, not forecast errors.

Three predetermined probes use all21 points on **June27, September24,
December23,2024**. Maximum3 HTTP attempts, no retries/date substitutions,
40s per request,256KiB per response and1MiB total. Stop on first failure,
preserve partial files. Check schema, coordinates, UTC units and24 distinct
valid hours per forecast target day using P0's parser. Do not compute residuals.
Three successes establish only those three dates; they do not certify180/607.

## Conservative API budget

[Pricing](https://open-meteo.com/en/pricing) explains fractional weighting for
long requests and permits evaluation/prototyping. [Terms](https://open-meteo.com/en/terms)
list free-tier limits below10000/day,5000/hour,600/minute. This is local offline
evaluation, not authorization for commercial serving or a paid subscription.

For one model/variable reserve `locations * max(1, days/14)` units. The floor
is a **conservative planning assumption**, not a provider usage receipt or a
complete specification of every endpoint's rounding. A short21-point request
therefore reserves21, not12 units. Recheck terms before implementing acquisition.

P0's frozen report recorded an estimated2299.5 weighted calls. Re-estimating
with this floor gives **2407.5** (+108). Add the previously reported single-point
access probe and round up: **2409** prior own-weather units. Keep the original
P0 report/manifest unchanged; this is an appended budget correction, not a new
quality result and not measured billing.

- Three probes reserve63 units; they are part of the planned cache, not free
  requests. Reuse their exact pinned bytes if the eventual cache accepts them.
- First180 issues reserve3780; whole cache needs12516 if11 reuses validate,
  or12747 without reuse. No automatic retries; failed attempts still count.
- Internal caps:8000/day,4000/rolling hour,400/rolling minute. Assume1000/day
  for other shared traffic. This reserve is not measured usage. If actual other
  traffic is higher, stop/replan; do not rotate IPs/keys to evade limits.
- With2409 prior units on the first UTCday, daily allotments are218,333,45 new
  attempts (if reuse passes): **three UTC-day allotments**. This is arithmetic,
  not a promise of three elapsed days or evidence that a worker is scheduled.
  The180-run first slice fits the first allotment if other usage stays in reserve.
- Each hour allotment <=4000 units. A future worker must persist actual attempt
  timestamps and enforce rolling windows, at least4s between starts, response
  limits and a stop on429. Calendar allotments alone do not enforce any quota.

## What still precedes P1 scoring

Independent design review; verify candidate reuse/source coverage and actual
counts; implement and test the quota-aware resumable cache acquisition; freeze
separate acquisition/evaluation protocol. Preserve raw IFS and four baselines,
all21 points and fallback counts. No quality statement follows from a successful
access probe. Observed P0 outcomes remain exploratory; no untouched holdout,
original publication, thermometer accuracy, live M3 or30-day claim is created.

Private archives, production, IEG/R0 and key access are outside this work.
