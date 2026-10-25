# Companion112 collector

Separate namespace; original parent state, pause, raw files and receipts remain
immutable. This implementation is a candidate until its source, independent
review, tests and exact execution-policy SHA are frozen. No HTTP is authorized by
importing it, constructing it or creating the metadata contract alone.

The policy binds canonical parent/output paths, exact clean execution commit,
stopped-parent state/plan hashes, all six parent policy hashes, and four metadata
bundle hashes (`science.json`, `inventory.json`, `contract.json`,
`authorization.txt`). The contract is rebuilt from those inputs before use;
all494 parent byte receipts are checked. The same user replacing ancestor paths
or submitting a wholly different trusted policy is outside this local model.

Initialization creates a new private directory with separate raw, failures,
metadata, policy and state; existing namespaces are never reset. An interrupted
initialization needs review. Existing parent `controller.lock` then `worker.lock`
are held throughout initialization and each step. Another authorized invocation
cannot proceed concurrently. Rolling budgets combine all prior events and new
reservations, including prior allowances and the already consumed diagnostic.
Changing a namespace or UTC date does not reset expenses. Max112 new attempts,
2352 new units; combined597attempts/15009units. Caps400/60s,4000/hour,7000/24h,
4seconds after response. No sleeps inside the worker; quota_wait reports a time.

Each step uses only the next date in the exact ordered112-date contract, gets its
URL from the pinned original plan, reserves durably before transport, and checks
the source guard again before the existing bounded fetch (40s, hard45s child).
It retains bounded returned bytes before weather-grid parsing. A receipt is
created only after original21point/7lead validation. Error-response prefixes,
when provided by the existing fetch, go into `failures`, never weather receipts.
Sanitized status/class/returncode and bounded prefix size/hash/truncation are
retained separately. The charged pause is durable before diagnostics are written;
missing diagnostics after a disk failure or crash never permit a retry.
Any error or uncertain in-flight reservation stops without retry or adoption of
an orphan response. Even fully received112 means pending separate606+gap audit;
it does not export, calibrate, compute scientific metrics, close liveM3 or deploy.

CLI `scripts/forecast_replay_gap_collect.py` takes parent/root/policy paths, policy
SHA256 and execution commit. `--initialize --bundle-dir DIR` only initializes;
without initialization it performs at most one request, never loops/retries.
The CLI always guards exact HEAD and a clean checkout. A separately reviewed
bounded dispatcher and frozen execution policy are required for real collection.
Never hand arbitrary model output to this CLI. The frozen policy/clean source
requirements complement review; they are not an OS sandbox or billing audit.
