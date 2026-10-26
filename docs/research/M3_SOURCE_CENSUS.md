# Source-coverage census v1 (111 dates)

Status: implemented and tested offline. Not executed. No request has been made
under this module, and no census namespace exists. Execution needs a separate,
explicit approval that names the final commit and the execution-policy SHA256.

## What it does

`app/services/forecasting/replay_source_census.py` collects the 111 issue dates that
remain after the parent (P) ledger and the companion (G) ledger, which is
`G/contract.json` acquisition calendar without 2025-08-06. Exactly one reserved
GET per step, no retries, no sleeping. A quota wait returns `not_before`.

- Expense history: `P/state.json.events`, `G/state.json.events` and
  `C/state.json.events`, each read once. Frozen ancestors must total 486 attempts
  and 12678 units. Ceilings are 111 attempts and 2331 units for the census, and
  597 attempts and 15009 units combined.
- Rolling limits 400/60 s, 4000/3600 s and 7000/86400 s, plus at least 4 s after
  the finish of the previous response, including a recorded HTTP 400. Quota uses
  the existing `CacheWorker.wait_until` over the complete combined history.
- Locks: P `controller.lock`, then P `worker.lock`, held for the whole call.
- Order per step: locks, pins and receipts, state and calendar, clock and budget,
  source guard, quota, durable reservation, second source guard, one GET.

## Terminal outcomes

- `fetched_ok`: bytes are stored, re-read by size and SHA256, and accepted by the
  original weather validator for all 21 points and 7 leads. Only then is the
  terminal receipt written.
- `recorded_run_unavailable`: only for HTTP 400 from `HTTPError` with a complete,
  untruncated body. The body must be strict JSON with no duplicate keys, with
  exactly the keys `error` and `reason`, `error` equal to true, and `reason`
  exactly `The requested model run is not available. Model: <model>, run:
  <reserved day>T<hour>:00Z`, where model and hour come from the pinned request.
  The body and a diagnostic receipt are written and re-read before the terminal
  status. This records the source response; it does not prove permanent absence.
- Anything else is a global STOP. The pause is durable before diagnostics are
  written. A reservation left without a terminal status is a STOP at the next
  run. No orphan is adopted and nothing is retried.

## Publication

`publication` is read-only and prints the calendar of all 607 issue dates with
provenance (`fetched_ok`, `recorded_run_unavailable`, `unrequested`,
`reserved_or_stop`), the number of valid runs, and either per origin x point x
lead pair counts or `not_computed` with a reason. Pair counts need a pinned
observed-actual archive, which census v1 does not bind, so the CLI reports
`not_computed`. Weather values, forecasts, errors, metrics and exports are not
published. The final status, when all 111 are terminal, is
`census_complete_pending_scientific_decision`. v1, strict607 and live M3 are not
declared.

## CLI

`scripts/forecast_replay_source_census.py` takes `--parent`, `--gap`, `--root`,
`--policy`, `--policy-sha256` and `--execution-commit`, and one of:

- `--initialize --bundle-dir DIR` creates the new namespace (no request).
- `--preflight --bundle-dir DIR` verifies pins and budgets offline (zero requests).
- `--publish` prints the publication (read-only).
- no mode: exactly one step.

Exit codes: 0 success or verified, 3 paused (a STOP), 2 refused. The source guard
requires HEAD to equal the execution commit and a clean worktree.

## Deliberate interpretations

- The inherited-manifest file is stored in the namespace as
  `inherited-manifest.json` next to `contract.json`. §2 lists the namespace files
  without it, but each step must re-verify the inherited bytes from the manifest.
  This needs review.
- The HTTP 400 body must have exactly the two keys `reason` and `error`. §3 does
  not name extra keys; an extra key is refused.
- Refusals that involve no state change (pins, schema, clock before reservation)
  refuse without transport when the saved namespace cannot be safely validated. Clock or source-guard failures in an otherwise valid initialized run persist a sticky pause even before reservation, without charging a request. Every unfinished reservation remains non-repeatable.


## Review hardening

Resume revalidates fetched census payloads with the original weather parser,
checks the full unavailable diagnostic schema and bindings, and reapplies the
strict classifier to its saved body. Offline preflight also checks an existing
namespace and refuses paused or uncertain state. Publication rejects caller-supplied
actuals because v1 binds no actual archive; its output remains not_computed.
A failed clock cannot create a timestamp: a sticky pre-reservation pause retains
the last validated journal time. Corrupt or unwritable namespaces are refused.
The raw/receipt manifest is intentionally kept alongside the contract in C and
externally pinned. Extra JSON keys in HTTP400 fail closed. None of these checks
attests scientific admissibility or gives permission to perform GET.

Pre-reservation STOP also writes a durable global-stop.json marker; editing only state.paused cannot resume requests. Existing-root preflight verifies the saved bundle as well as the supplied bundle.
