# M3 replay P0 — bounded exploratory feasibility experiment

Frozen configuration: `M3_REPLAY_P0.json`, committed before the first download of
verification outcomes for this experiment. This is an **exploratory design**,
not a retrospective claim of preregistered confirmation. Related outcomes were
already inspected in M3-A. The old negative result and live M3 are unchanged.

## Question and population

Is an uncalibrated API-processed IFS forecast worth further investigation on
daily mean temperature at all 21 `openmeteo-points-v1` coordinates, against
Naive, SeasonalNaive(7), MovingAverage(7) and Climatology(±3 days)? No candidate
selection, calibration tuning, statistical success test or promotion occurs.

The twelve test windows are **days 1–7 of each month of 2025**. Origin is the
previous month's last day at 23:59:59 UTC; the requested IFS run initialises at
00:00 UTC on that same origin date. Thus a whole seven-day trajectory comes
from one run. An eight-day response includes the origin day, which is not
scored. Complete training prefixes begin 2022-01-01 and end on each origin day.
No baseline sees an evaluation day in its fitting input.

## Verification and interpretation

Verification is hourly ERA5 at the requested coordinates, daily-averaged in UTC,
from a newly retrieved fixed vintage. ERA5 is a **modelled reanalysis**, not
station truth, and its revised training values were not necessarily available
at each historical origin. IFS provider cycles/historical publication are not
attested by these JSON responses. Every result is therefore labelled:

> pseudo-real-time reconstructed vintage; not a real-time claim; not live M3

There are 12 sampled weeks of one calendar year, not 12 consecutive live M3
windows, two seasonal cycles, or evidence for the 30-day horizon. Raw here means
**uncalibrated API output**, not native unprocessed GRIB fields. Provider
interpolation/downscaling may be present. Forecast and verification grid cells
need not be identical: each must be within 0.3° of the requested coordinate,
and returned coordinates are retained in the source bytes.

## Acquisition and refusals

Only `archive-api.open-meteo.com/v1/archive` and
`single-runs-api.open-meteo.com/v1/forecast` over HTTPS are used. No production,
old private archives, credentials, database, models directory or user stash.
This is bounded offline evaluation/prototyping, not commercial serving. The
[pricing page](https://open-meteo.com/en/pricing) permits free-tier evaluation
and prototyping; [terms](https://open-meteo.com/en/terms) limit free service use.
Data attribution: Open-Meteo and ECMWF ERA5/IFS, CC BY 4.0. No subscription or
commercial usage entitlement is inferred from a successful request.

Thirteen logical multi-point requests, at most one transient retry each;
limits are frozen in JSON. Period/location-weighted call estimates are reported
separately from HTTP counts. Redirects and proxies are disabled. Each response
is bounded; existing output directories/files are refused. A failed acquisition
leaves its partial directory without a complete manifest, never silently resumes
or overwrites it. Do not repeatedly retry the whole experiment.

Input must declare °C, ISO8601, GMT/UTC and integer UTC offset zero. Hour strings
must be naive `YYYY-MM-DDTHH:00`, interpreted only after that declaration. Refuse
duplicate keys/hours, nonnumeric or nonfinite values, different array lengths,
wrong location/order, bounds, units or zone. Nulls are missing. Every declared
verification point/day must have at least 19 distinct hours; scored forecast
days require all 24. The unscored origin day is not a coverage requirement.
A required day with no rows is absent and refuses. No point/window/method
is dropped, no interpolation is performed by this code.

The manifest pins the protocol, request parameters, raw response bytes,
retrieval times and acquisition commit. Parsing and hashing share one raw read.
Audit/evaluation additionally require the manifest digest supplied independently
on the command line. A hash is not proof of historical publication. These are
local reproducibility checks, not a sandbox against a malicious same-user actor.

## Evaluation and reproducibility

Reuse `daily_observations`, existing baseline implementations and
`evaluate_rolling_origin` with one window per declared origin. Each method uses
the identical point/day grid. Primary metric is macro MAE (equal point weights);
also retain every point/window's RMSE, MASE and actual-minus-predicted bias.
MASE's lag-7 scale comes only from that window's training prefix. Keep the worst
point visible. No interval-coverage, significance, baseline-beating or live
success verdict is emitted; the report always remains exploratory.

Commands use the repository's Python environment:

```text
python scripts/run_forecast_replay_p0.py plan
python scripts/run_forecast_replay_p0.py acquire NEW_EXTERNAL_DIRECTORY
python scripts/run_forecast_replay_p0.py audit DIRECTORY --manifest-sha256 PIN
python scripts/run_forecast_replay_p0.py evaluate DIRECTORY --manifest-sha256 PIN --output NEW_REPORT.json
```

Tracked sources/protocol must be committed and clean before acquisition or
evaluation. Reports record acquisition/evaluation commits, protocol digest,
manifest digest and all windows. Output files must be new. Refusal exits 2;
it is not an empty successful benchmark. Actual acquired snapshot pins and
results belong in the run evidence, never inserted as predictions into production.

## After the result

Publish the complete descriptive comparison, including losses and source
refusals. Determine whether a larger, separate development experiment or a
past-only calibration is justified. Do not tune on these twelve test windows
and relabel a rerun confirmation. Any live shadow use, product integration or
promotion follows separate review; live M3 collection continues independently.
