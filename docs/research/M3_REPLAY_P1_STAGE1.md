# M3 P1: first bounded exploratory slice

This offline adapter compares one predeclared January2025 week at all21
`OPENMETEO_POINTS_V1` locations. It is a development experiment, not an
untouched holdout, a forecast service or evidence that the live M3 clock ended.
Related2025 outcomes were already inspected. No statistical pass is defined.

## Frozen comparison

- Origin2024-12-31 23:59:59UTC; target daily means January1–7,2025.
- IFS00UTC run from the origin day; each target needs24 valid distinct hours.
- Retrieved ERA5 modelled temperature, not thermometers; daily means need19hours.
- Corrections use exactly180 predetermined common issuance dates June27–December23,2024,
  for each point/lead. This is a bounded subset, **not the complete365-day calendar**.
  Each target must have ended before the evaluation origin. Historical publication
  availability of revised ERA5 and the exact historical IFS cycle are not attested.
- Correction is mean(actual−forecast), with a365-day issuance window and minimum180.
  The adapter refuses an incomplete slice; it never drops a point or silently mixes
  fallback with a purported complete calibrated comparison. The generic correction
  component still supports explicit fallback for other uses.
- Six methods: raw IFS, corrected IFS, Naive, SeasonalNaive7, MovingAverage7,
  Climatology±3days. Baselines fit only the prefix2022-01-01 through origin.
- MAE/RMSE/MASE/bias, all21 points equally weighted. MASE uses training-only
  seasonal-lag7 scale. MacroRMSE averages pointRMSE; per-leadRMSE pools21 point
  errors for that lead. Both are explicitly descriptive.
- All147 test targets, all26460 training pairs, per-cell maturity/count/hash,
  per-point/per-lead metrics, raw fallback count and worst point are recorded.
  The constant mean-bias correction is not an MAE optimizer. Negative outcomes stand.

## Export and execution

`forecast_replay_stage1.py` is an explicit offline CLI. `export` accepts one
already settled cache and independently pinned P0 sources. It creates a new
`.partial` directory on the target filesystem, fsyncs member files/directories,
then publishes via rename and fsyncs the parent. Pre-rename failure keeps partial
artifacts; post-rename fsync failure leaves the snapshot and explicitly reports
unconfirmed durability. Existing directories/files are never cleaned or overwritten.

The standalone export has180 training responses plus the pinned verification and
one test-run response. Its complete-stage1 flag never means the607-date cache is
complete. Original P0 inputs/report remain unchanged. All bytes parsed are checked
against an independently supplied manifest pin; filenames/order/calendar are exact.
This protects accidental corruption and drift, not a hostile same-OS-user writer.

```
python scripts/forecast_replay_stage1.py export \
  --protocol /approved/protocol.json --protocol-sha256 EXTERNAL_SHA256 \
  --cache /approved/cache --p0 /approved/p0-snapshot --output /new/stage1-snapshot
python scripts/forecast_replay_stage1.py evaluate \
  --protocol /approved/protocol.json --protocol-sha256 EXTERNAL_SHA256 \
  --snapshot /approved/stage1-snapshot --manifest-sha256 EXTERNAL_SHA256 \
  --output /new/stage1-report.json
```

Protocol/source pins are supplied from the separately reviewed execution freeze,
not computed from untrusted inputs as their own trust anchors. The CLI requires
committed sources and writes a new report exclusively. No HTTP, database, scheduler,
model persistence or promotion path is registered. Full12-window P1 and live M3
remain separate work. A second independent code review is still needed before
publishing/integrating this local draft.
