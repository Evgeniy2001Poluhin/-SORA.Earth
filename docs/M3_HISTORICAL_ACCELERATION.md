# Historical acceleration of forecasting — research design draft

Date: 2026-10-22. Base inspected: `6a840f8e`.

This is a focused research proposal and offline preflight tool, **not a second
roadmap, an adopted scientific declaration, or a completed forecast experiment**.
It does not amend `M3_FORECAST_DECLARATION.md`, `M3A_ARCHIVE_DECLARATION.md`,
`M2_EVALUATION_PROTOCOL.md`, the live clock, candidates, scheduler or promotion.

## Decision

Use historical data to accelerate the decision about a useful forecasting
product. Keep the existing live M3 as prospective evidence. Develop a separate,
explicitly retrospective experiment; prioritise the 7-day horizon. Treat the
30-day horizon as a separate feasibility decision, not a compulsory promise.

The useful next question is whether an archived numerical weather forecast,
possibly with a small causal calibration, is better than simple alternatives.
The previous candidates forecast weather from past temperatures alone. An NWP
run supplies future atmospheric dynamics rather than another lag-only model.
Whether this improves the declared metric remains unmeasured.

**Fastest executable research route:** use a pinned reconstructed vintage for
an exploratory replay, explicitly labelled pseudo-real-time. Historical
publication evidence is not required to ask that weaker question. It is required
to claim original-run availability at each past origin. Keep these modes separate
rather than either forbidding useful retrospective research or backdating it.

Three distinct milestones must remain visible:

1. **Historical feasibility:** reproducible evaluation under a declared vintage
   and exposure policy. This can be investigated without waiting for the live
   clock, once data access/provenance are verified.
2. **Operational readiness:** forward shadow forecasts, durable storage,
   publication latency, recovery and missing-input handling. Several successful
   cycles prove plumbing only, not annual skill.
3. **Prospective skill:** the existing M3 windows and scientific rules, or a
   separately reviewed amendment. A retrospective pass cannot mark this done.

## What has already been tried

The M3-A v1.2 report recorded in the shared council on 2026-10-12 used the
2018-01-01–2026-10-10 fixed archive, 21 points, 104 seven-day windows and 24
thirty-day windows. None of its four claims passed. These are **author-reported
results**, not an independent new scoring in this work:

| Horizon | Candidate / reference | Macro MAE | Registered outcome |
|---|---|---:|---|
| 7 | xgboost_lag / Climatology | 2.224 / 2.335 °C | Upper paired difference bound +0.007 °C: no success |
| 30 | xgboost_lag / Climatology | 3.084 / 2.337 °C | Candidate worse: no success |

Do not round +0.007 below zero, loosen alpha, select winning points, or overwrite
the report. The observed climatology bias is not a universal correction constant.
The 2018–2026 archive is development/exploratory material after these inspections;
a new model name or predictor source does not make its known outcomes untouched.
The earlier design note `M3A-2-RESEARCH-DECISION.md` remains consistent with this.

## Public source investigation

Sources checked on 2026-10-22; published coverage is not a measured complete
download. No production, private weather archive or IEG data were read here.

| Source | Suitable use | Limitation / next check |
|---|---|---|
| [Open-Meteo Historical Forecast](https://open-meteo.com/en/docs/historical-forecast-api) | Reuse the existing fixed-vintage series for exploratory comparisons | Stitches early hours of successive runs. It is not a seven-day-ahead forecast issued once at an origin; model versions vary. |
| [Open-Meteo Single Runs](https://open-meteo.com/en/docs/single-runs-api) | Candidate acquisition route for a whole 7-day trajectory from one run | Documentation lists IFS from March 2024, other models from April 2026, and mentions IFS hindcasts. Establish original-vs-reconstructed identity per segment. Initialisation is not publication; neither is a request made today. The documented ordinary horizons do not supply 30 days. |
| [Open-Meteo Previous Runs](https://open-meteo.com/en/docs/previous-runs-api) | Exploratory error by fixed 1–7-day lead; possible bias-calibration data | A daily mean of `_previous_day7` may combine different initialisations. Do not substitute it for a whole horizon frozen at one origin. |
| [NOAA NCEI GFS](https://www.ncei.noaa.gov/products/weather-climate-models/global-forecast) | Fallback investigation of original operational-run archives | NCEI lists archived analysis/forecast products. Verify the actual requested forecast hours and archive access; do not assume that a cloud rolling archive retains all old runs or that analysis files are forecasts. GRIB extraction adds implementation work. |
| [ECMWF S2S real-time archive](https://www.ecmwf.int/en/forecasts/datasets/s2s-global-sub-seasonal-seasonal-ensemble-real-time-forecasts-multiple-nwp-and-research-centres-2015), [reforecasts](https://ecds.ecmwf.int/datasets/s2s-reforecasts?tab=documentation), [licence](https://ecds.ecmwf.int/licences/s2s-licence) | Investigate 30-day feasibility using a genuine long-horizon ensemble | Real-time products and retrospective reforecasts are different. Published typical horizons are 34–65 days; centre, variable, time aggregation, registration and access delay must be checked. Current access delays are not proof of historical delivery to this product. Do not acquire keys or subscribe automatically. |

**One bounded public forecast-only probe was performed.** Single Runs returned
HTTP 200 for Berlin, `ecmwf_ifs`, run `2024-06-01T00:00`, hourly temperature,
8 forecast days: 192 timestamps from June 1 00:00 through June 8 23:00, UTC,
°C. Response-byte SHA-256 was
`eefbc1b004a1882a97b0aaa3dd863715a446407953630d695302860da9af9ed7`.
Only receipt/metadata were retained in the supervision evidence, not weather
values or a training snapshot. This is an access/schema check, not a pinned
dataset, proof of completeness at other points, original-run authenticity,
historical publication time, forecast skill, or permission to commercialise.

## Recommended 7-day experiment

Freeze a separate declaration and configuration **before scoring**:

- **Population:** the existing `openmeteo-points-v1`, all 21 points, with fixed
  coordinates. Do not silently drop a point whose archive is harder to obtain.
- **Target:** daily mean temperature on the next 7 complete UTC days from one
  origin. State whether verification is the old modelled series, reanalysis, or
  station observations. Those are different estimands. No thermometer-accuracy
  claim from a modelled verification target.
- **Sources:** one pinned operational model/version per declared segment. Keep
  raw bytes, request parameters, model initialisation, documented availability
  evidence, retrieval time, transformations, units and digest. Hindcasts get a
  separately labelled experiment; do not relabel them original issued forecasts.
- **Exposure:** begin on the old inspected period as **exploratory**. Enumerate
  all earlier scoring, decisions and downloads in an exposure log. A genuinely
  untouched holdout needs evidence of nonexposure and a frozen selection rule;
  otherwise no confirmatory language, even for a different algorithm.
- **Minimum comparison:** Naive, SeasonalNaive(7), MovingAverage(7), Climatology,
  and the **uncalibrated NWP forecast itself**. A calibrated model must earn its
  complexity against the raw forecast, not only the easiest time-series rule.
- **First candidate:** one fixed additive bias correction per point and lead
  day, mean of training-prefix verification-minus-NWP residuals. Predeclare a
  trailing 365-day calibration window, at least 180 available training pairs per
  point/lead, and refusal on insufficient history. This is a design proposal,
  not a fitted candidate or a tuned optimum. All training labels and forecasts
  must have been available by the origin; a final reanalysis download cannot be
  treated as a then-available training label. Reconstructed-vintage calibration
  remains pseudo-real-time with that limitation explicit.
- **Splits:** expanding rolling origin with nonoverlapping 7-day test windows;
  freeze start/end and every exclusion from availability-only inspection. Prefer
  two full seasons/year cycles. Do not stop at the first attractive interval.
  For development, use nested time-ordered splits; training labels must end
  before the next origin (purge any examples whose outcome is still in future).
- **Evaluation:** reuse `evaluation.py` and the date-pairing/circular block
  bootstrap machinery of `archive_eval.py`; do not build a competing evaluator
  or mutate its v1.2 constants. Every method scores the same point/window grid.
  Report macro MAE, RMSE, MASE, bias, every point, worst point and source segment.
  Report missing windows/refusals rather than average only surviving cases.
- **Inference:** exploratory first, with no new success claim. A later
  confirmatory design freezes candidate count, effect threshold, family-wise
  alpha, block length, resamples, seed and windows **before** untouched outcomes.
  If the draft tests one candidate at two horizons, use alpha/2=0.025 per claim
  within that new family and intersection–union across all baselines. This
  cannot be applied retroactively to M3-A's four-claim family. Freezing only
  one horizon must be a design choice, never the result of scoring the other.
- **Uncertainty:** empirical in-sample one-step residuals are not validated
  seven-day coverage. Use past-only rolling calibration, evaluate intervals by
  lead/point, and make no calibrated-interval claim before measurement.

The 365/180 calibration requirements are proposal parameters requiring review
and feasibility measurement. They are not copied into live entry conditions.
If source coverage cannot support them, record a protocol revision before
scoring, or first evaluate raw NWP without fitted calibration. That is a useful
historical feasibility result even if no ML is promoted.

## 30 days: separate decision

Do not extrapolate the last day of a 10/16-day forecast to claim a 30-day NWP
trajectory. Do not turn monthly seasonal means into daily observations. First
check S2S centre/model/version, all 21 points, daily 2m temperature/member data,
units, coverage, archive/reforecast status, licence, access and size. Then choose
and freeze either the existing **daily-trajectory** target or a separately
declared **weekly/monthly anomaly** product. These claims are not interchangeable.

If access or honest skill is inadequate, retain climatology as a labelled
reference/context and withhold a 30-day actionable-forecast claim. This does not
block shipping a demonstrably useful 7-day product. Neither a negative
experiment nor a baseline-only product is a reason to manufacture a new model.

## Offline preflight delivered with this draft

`scripts/check_forecast_replay_readiness.py PLAN.json` reads supplied metadata
only. It checks explicit UTC instants, initialisation <= availability <= origin
for original operational runs,
retrieval consistency, SHA-256 pins, original-run/receipt declarations, frozen
model version, complete hourly horizon from one run, nonoverlapping windows,
every declared point/origin and holdout exposure. Reconstructed vintages instead
require an explicitly exploratory role and a retrospective retrieval basis;
they receive a pseudo-real-time label and cannot pass as original-run
confirmation. Duplicate JSON keys refuse.
Exit **2** means refusal. Exit **0** means metadata consistency only.

Top-level fields are `schema_version` (1), `study_role`, `outcome_exposure`,
`source_kind` (`operational_archive` or `retrospective_reconstruction`),
`availability_basis` (`contemporaneous_receipt` for the former,
`retrieved_vintage_only` for the latter), `provider`, `model`, `model_version`,
`verification_target`, `target` (`temperature_2m_daily_mean_utc`),
`temperature_unit` (`degC`), `horizon_days` (7 or 30), `points`, `origins`, `runs`,
and four lowercase 64-hex pins: `forecast_snapshot_sha256`,
`verification_snapshot_sha256`, `availability_evidence_sha256`,
`exposure_log_sha256`. Each run has `point`, `origin`, `initialised_at`,
`available_at`, `retrieved_at`, `model_version`, `payload_sha256`,
`availability_receipt_sha256`, and the exact list `valid_hours` for the scored
horizon, selected from its full payload. Timestamps must carry UTC explicitly.
For a reconstructed vintage, `available_at` records verified availability of
that reconstruction (which may be today), not a fabricated original publication
time; its receipt is pinned too. The tool bounds plans to 21 points, 520 origins
and 16 MiB of JSON. These are resource limits, not scientific entry criteria.

It **does not authenticate** receipts or recompute pins against archive bytes,
verify temperature values, label availability/coverage, population equivalence,
minimum scientific window count, training isolation, statistical power or skill.
The output explicitly says those checks/promotion are not established. A small
synthetic fixture passing is not the 21-point experiment passing. This is an
early refusal tool, not a replacement for the existing scientific gates.

## Execution and remaining gates

| Step | Concrete outcome | Current state |
|---|---|---|
| 1 | Preserve v1.2 failure; investigate sources; public schema probe; synthetic preflight | Implemented locally in this branch; raw data not scored |
| 2 | Review draft; inspect provenance/coverage without joining predictors and outcomes; freeze source/model/population and licence | Pending independent review and actual source audit |
| 3 | Build a dedicated weather-only pinned snapshot with size/request limits and exposure log; verify bytes and whole grid | Pending; existing private archives remain untouched |
| 4 | Implement raw-NWP adapter and, if eligible, the one declared past-only calibration; synthetic leakage tests | Pending source/schema decision, reuse existing evaluator |
| 5 | Run the frozen exploratory replay once; record code/declaration/config/snapshot SHAs and all results | Pending; no training/evaluation in this task |
| 6 | Decide go/no-go and whether an actually untouched confirmatory evaluation exists | Pending measured results; a negative result is accepted |
| 7 | Forward shadow bridge; measure retrieval, fallback, durability, forecast/label matching and recovery | Local design first; production actions remain separately gated |

This sequence removes waiting for live accumulation from **historical feasibility
and development**. It does not promise a completion date, a positive result,
instant access to authenticated archives, or that an operational shadow period
replaces prospective skill. Live collection/backup continues throughout.
