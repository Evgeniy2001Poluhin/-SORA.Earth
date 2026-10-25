# Full P1 tools: twelve exposed development windows

Local research tools only. No application registration, model promotion,
deployment, database/private archive access, or live-M3 clock changes. This
change contains no real acquisition or quality result. P0 and the first slice
remain immutable, including the negative result for the fixed correction.

## Offline snapshot and scientific contract

`replay_full.py` exports the whole predeclared calendar: 607 daily IFS runs
from 2024-04-01 through 2025-11-28, plus the original ERA5 verification file and
twelve P0 test forecast files. These 620 raw members and their manifest are a
new standalone artifact. Repeated forecast bytes in training/test roles are
intentional; no new HTTP is needed for the existing P0 files.

Export requires a complete, settled shared cache and both externally supplied
recovery/continuation pins. It checks the original worker identity and pinned
acquisition execution commit, validates exactly the copied buffers, writes and
fsyncs members/manifest into a new `.partial`, then renames and fsyncs the parent.
Failure before rename preserves `.partial`; failure after rename explicitly
reports that the snapshot exists with unconfirmed durability. Nothing is
overwritten or removed. Loading rechecks the external manifest pin, exact member
order/hash/size, P0 semantic pin and complete parsed hourly point/day grids.

Evaluation retains all 21 coordinate points, six methods and twelve original
P0 origins: seven forecast days at the start of each month of 2025. Missing or
invalid data refuses the complete run. The points are samples near cities, not
country-level forecasts. Hourly-to-daily parsing and all four baselines are
the existing P0 implementations.

For each origin/point/lead, fit the original mean(actual−IFS) using issuance
cutoffs in [O−365 days,O) and only targets whose complete UTC day ends ≤O.
Minimum history stays 180 pairs. Insufficiency is an explicit zero-correction
raw fallback, reported per cell and retained in every aggregate. Nominal complete
coverage should need no fallback, but the strategy does not hide it. The full
January history is larger than the earlier 180-run slice; a different fitted
offset is expected without changing the formula. Both results are retained.

All baselines and MASE scales use only the actual-data prefix through the origin.
Future test labels can change errors but cannot change that origin's fits,
predictions or MASE denominator. Earlier test outcomes may enter later prefixes
after maturity; these exposed outcomes are not an untouched holdout. Revised
ERA5 event-time completion is not proof of historical publication availability.

Primary aggregation is equal weight across points and the twelve seven-day
windows. Macro RMSE averages point-window RMSE; by-lead RMSE pools errors across
points/windows. Report all metrics, daily vectors, corrections, pair counts,
selection hashes, MASE scales and fallback counts. No significance intervals,
candidate selection, promotion or live-M3 success follows from this comparison.

Before publishing a result, the CLI requires the original P0 result's external
SHA256 and compares unchanged raw IFS/all four baselines against it at macro,
point and point-window levels. A mismatch refuses publication. A new report path
is required before loading/scoring, so an existing result is never recalculated
and overwritten. Independently recomputing arithmetic from raw bytes remains a
required operational check after the eventual real run; synthetic tests and
reference equality are not that independent real-data verification.

## Explicit bounded acquisition controller

`replay_acquisition.run_batch` accepts only an already authorized continuation.
It never authorizes, initializes or resets the ledger. A controller lock prevents
two batches at once; each step also uses the existing worker lock. Both pins,
the exact committed source and the old attempt/receipt prefix remain required.

A batch allows at most 40 successful new requests and a 1–600 second deadline
for starting another step. An in-flight bounded transport may finish after that
deadline; it is not forcibly killed into an ambiguous state. The worker's child
transport has its own 45-second deadline. CLI checks exact clean HEAD before
each step and again at the transport boundary. If source changes after reservation,
the conservative reservation remains spent and the journal pauses.

Success waits four seconds before another step. Quota waits, failures and local
crash recovery are returned immediately to the operator rather than hidden in a
sleep/retry loop. Rolling 400/60s, 4000/hour and 7000/24h accounting is unchanged;
midnight is not a reset. There is no daemon or scheduler in this change.

## CLI and execution order

`scripts/forecast_replay_full.py` provides:

- `batch`: explicit cache, both pins, exact source commit, request/time limits.
- `export`: independently pinned full protocol plus cache/P0 paths and a new output.
- `evaluate`: pinned protocol/snapshot/reference and a new output; strictly offline.

The full protocol pins separate acquisition and analysis commits. If this tool
commit supersedes an earlier unactivated acquisition-policy draft, prepare a new
policy and pin for the same settled checkpoint before authorization; do not
silently edit the earlier policy. Once network acquisition starts, keep its
execution checkout frozen. Prepare any further code on a separate own branch or
checkout and never change an in-flight worker's source.

Before real execution: freeze source/protocol/pins and budget, verify the existing
191 receipts/178 attempts, authorize only the remaining 416 dates on the same
ledger, run bounded batches, audit the complete grid, export once, evaluate once,
independently verify and report. These steps are not performed by merely preparing
the tools. Production/integration remain separate owner gates.
