# P1: design for retrospective temperature bias calibration

Status: local implementation and synthetic verification only. No data acquisition,
fit on real pairs or quality evaluation is part of this change. Source/cache
feasibility and independent review still precede any P1 experiment freeze.

## Why this is separate from P0

P0 is frozen on commit `3868945c24c05b9c91307ed4dc5d330be483e276`.
Its 2025 outcomes and point-level results have already been inspected. P1 on
that period is development/exploratory, even after this candidate is committed.
Keep the original P0 report; never overwrite it with a calibrated result.

Raw IFS beat all four baselines at 17/21 points in that sample. It lost to all
four at IDN, KEN, MEX and NGA. All 21 remain in the population; no point-specific
choice of algorithm or parameters. Near-zero mean bias at KEN already warns
that an additive offset need not repair its errors. This is one candidate to
test, not a promised improvement or a new model for the application.

## Exact candidate implemented

`app/services/forecasting/replay_calibration.py` is a pure function module with
no IO and no application registration. Units are Celsius. Point membership is
`OPENMETEO_POINTS_V1`; leads are integers 1 through 7. No fitted model artifact.

- Issue cutoff: **23:59:59 UTC** on date D. The eventual source adapter must
  use the IFS **00:00 UTC initialization on D**, matching P0. No mixing newer
  initializations into the same issued horizon.
- Lead h targets the **whole UTC day D+h**. Target end is next midnight,
  D+h+1 at 00:00 UTC, exclusive. Input rows represent validated daily means,
  not hour-level pairs or arbitrary times. Forecast requires 24 distinct valid
  hours; ERA5 verification requires at least 19, using P0's existing parser.
- For evaluation cutoff O, a calibration pair is eligible only if its issue
  cutoff lies in **[O−365 days, O)** AND its target end is **≤O**. This purges
  overlapping not-yet-completed outcomes, including long leads issued before O.
  The issuance window is exactly 365×24 hours, not one calendar year.
- Estimate **mean(actual − raw forecast)** independently for every point/lead;
  add it to the raw forecast. No pooling, clipping, shrinkage, model selection,
  interval claim or lead-specific tuning. The mean targets average bias; it
  does not minimize MAE and can worsen it. Keep the declared choice rather than
  swapping mean for median after inspecting performance.
- Minimum **180 distinct eligible pairs** per point/lead. At 179 or fewer the
  result is explicitly `raw_fallback_insufficient_history`, correction zero.
  This preserves the full scoring population, but that cell is **uncalibrated**.
  Do not count fallback as successful calibration readiness. A comparison must
  report fallback counts per point/lead/window alongside the fixed-strategy
  score and retain a distinct raw IFS comparator. No dropping weak cells.
- Duplicate point/issue/lead keys refuse the input; missing rows stay absent,
  never zero-filled. Reject nonfinite values, booleans, unknown points/leads
  and non-UTC/nonstandard cutoffs. Sort eligible keys for reproducible summation.
- Audit output includes eligible count, first/last issue, latest target end and
  SHA256 of selected keys. That hash identifies selection only: it does not
  authenticate values, provider publication, source metadata or source files.
- A future origin may use outcomes from earlier P0 weeks only after they are
  complete. It must never fit on its own forecast targets. Such chronological
  reuse does not erase earlier exposure or create an untouched holdout.

The previous 365/180 proposal described refusal of insufficient-history
calibration. This design makes the candidate's refusal explicit and returns raw
IFS so that a *fixed fallback strategy* can later be evaluated without dropping
points. That strategy must be named and frozen before scoring; it is not the
same claim as a calibrated model at every point.

## Historical availability limitation

Completion of a target day is an **event-time filter**, not a publication receipt.
Final-vintage ERA5 can include revisions and publication latency. IFS historical
API responses do not attest old model cycle or original publication. This module
therefore labels every result **retrospective event-time-only calibration; not
historical availability; not live M3**. It cannot validate operational causality.
For an original-run/live claim require independently recorded forecast and label
availability ≤O; adding an arbitrary 24/48-hour lag does not prove availability.

The caller must pin original weather bytes, requests, coordinates/grid,
initialization, units, retrieval timestamps and transformations. Require one
documented forecast source/verification vintage per experiment, or declare
segments before scoring; the pure pair module cannot detect mixed vintages.
Do not turn a caller-supplied timestamp or hash into authenticity evidence.

## Next bounded work, before any scoring

1. Review the cutoff convention, minimum count and fallback strategy independently.
2. Plan a separate daily-run training cache. P0's 12 runs cannot supply 180
   pairs per point/lead. Check available date coverage and counts only, without
   comparing residuals or selecting dates from errors. Keep insufficient cells
   in the report. A source/coverage refusal is a legitimate feasibility result.
3. Compute weighted provider cost including multiple points, forecast duration,
   retries and previous usage. The P0 download cost an estimated 2299.5 weighted
   calls despite only 13 HTTP attempts. Do not issue hundreds of daily requests
   under the mistaken assumption each costs one unit. No keys or subscription.
   Freeze bounded batches and terms before any acquisition; no new HTTP here.
4. Adapter/source feasibility must prove exact point/initialization/target pairing
   and pinned cache provenance. A missing cache is not a completed P1.
5. Freeze a separate P1 acquisition/evaluation protocol and source segments.
   Keep raw IFS and all four P0 baselines on the same 21-point/window grid;
   report per-lead corrections, sample counts and fallback as well as MAE,
   RMSE, MASE, bias and worst point. Do not tune from these scores.

No change to live M3 entry gates, dates or negative M3-A results. No 30-day
claim follows from this seven-day design. No production, archive access,
training on real data, promotion or deployment is performed by this module.
