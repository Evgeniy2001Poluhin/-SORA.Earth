# Full-calendar continuation of the completed P1 first slice

Local candidate only. No real ledger authorization, HTTP request, fit or scoring
is performed by this change. The full twelve-window evaluator remains separate
work. This does not change live M3, the exposed first-slice result or production.

## Why a separate authorization

The first-slice recovery was intentionally bounded to one failed date plus its
remaining slice dates. Its external pin cannot authorize the remaining calendar.
`replay_cache_continuation.py` extends a **completed, settled** recovery without
resetting the ledger, its original source identity, counters or failure record.

At the current checkpoint 191/607 calendar runs are present, with 178 attempts
and 6210 conservative units including prior usage. The remaining 416 dates cost
at most 416 new attempts and 8736 additional units without retries. These are
local reservations, not provider billing or measured shared-IP usage. Runtime
derives the exact missing dates and totals from the independently pinned parent;
it does not trust hand-entered counts. Existing receipts are reused.

## Policy and journal contract

A separate policy must be reviewed and pinned before authorization. It contains
exactly the fields validated by `validate_policy`: schema/id, canonical ledger
path, parent checkpoint SHA256, original recovery amendment SHA256, new execution
commit, sorted missing dates, additional/total attempt and unit budgets, fixed
caps, zero retries, no scientific changes and stop-on-failure policy.

The authorizer checks the actual clean CLI HEAD against the supplied execution
commit. It validates the completed recovery using its original pin, refuses
paused/inflight/partial checkpoints and unreceipted raw files, then durably writes
the byte-exact parent checkpoint and policy **before** changing the live journal.
Only the authorization record and monotonic timestamp are added. The original
failed-state backup and recovery amendment are untouched. Interrupted offline
authorization can be retried with the same bytes; conflicting artifacts refuse.

Every subsequent operation requires **both** external pins. Parent attempts,
receipts and source/plan/recovery identities must remain identical. New attempts
must follow the exact missing-date prefix, have unique dates, reserve 21 units
durably before transport, and carry the continuation pin. A new completion needs
an actual continuation attempt. New imports remain prohibited during this phase;
idempotent imports of already completed bytes remain possible.

The existing 400/60s, 4000/hour and 7000/24h rolling limits and four-second spacing
after response completion are unchanged. UTC midnight does not reset anything.
Failed attempts remain spent. Failure stops the journal; reauthorizing an existing
continuation never clears that pause. After a crash, only complete bytes matching
the previously persisted response hash may be recovered locally. An uncertain
attempt without pinned raw never causes another HTTP request.

When all dates complete, the worker returns `all_downloaded_pending_final_audit`.
Only a full payload/grid audit yields `ready_for_review`; that is acquisition
readiness, not a new calibration result or scientific acceptance.

## Explicit CLI, not a daemon

`scripts/forecast_replay_cache_worker.py authorize-continuation` requires
`--policy`, `--policy-sha256`, `--recovery-sha256` and `--confirm-code-commit`.
It is offline. `status`, `audit`, `import-one` and `fetch-one` accept
`--continuation-sha256` alongside the original recovery pin. `fetch-one` is still
one explicit bounded request; this change introduces no background loop, retry,
subscription, credentials, alternate provider or quota reset.

Do not reuse the first-slice driver or change its immutable protocol to access
the new scope. Freeze the new execution code/policy before any new HTTP. Full
evaluation needs its own frozen adapter, source manifest and descriptive report;
all 21 points, six methods and twelve original P0 windows remain fixed. The first
slice and P0 stay separate and unchanged; no tuning from the negative slice.

The trust model is an owned local process and reviewed external pins, not a
sandbox against the same OS user replacing files or inventing another client.
