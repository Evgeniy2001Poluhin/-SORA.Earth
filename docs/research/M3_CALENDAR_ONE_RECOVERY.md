# Explicit recovery of one calendar streaming error

Local weather research only. This code does not authorize itself, retry on a
timer, change the scoring protocol, or touch production. Old source and policy
files, first-slice/P0 results, and all quota history remain immutable.

An optional third externally supplied SHA256 pins one new amendment over the
original recovery and continuation pins. Authorization requires a byte-pinned
paused checkpoint and the exact known `timeoutReached` streaming-error body.
Unknown bodies, dates, outstanding history, policy fields or budget changes
refuse before live-state mutation. The failed date must be the first still
missing date in the original continuation order. Only that date is repeated.

The old state and policy are made durable first. The exact bad raw response is
renamed to `failed-calendar-response.bin` in the same cache, both directories are
fsynced, and only then the new activation is saved. A crash before the state
write leaves the failure paused; offline authorization can resume with the same
pins. Files are neither deleted nor silently overwritten. Same-user malicious
filesystem races remain outside the trusted local-cache threat model.

The initial code/plan identity and original recovery record stay unchanged. The
old continuation activation record is preserved in `failed-calendar-state.json`.
The live continuation record's policy pin becomes the retry pin, so even frozen
pre-retry code refuses it before a network request. The new worker validates the
original continuation against the immutable failure checkpoint and requires all
three pins. It checks old events/receipts unchanged and the exact new ordered
attempt prefix. Removing the new record alone cannot make old code accept it.

Total attempt/unit limits are derived from the failed history plus the exact
remaining dates. Relative to the old successful-completion budget this adds one
request/21 conservative units. Rolling 400/minute, 4000/hour, 7000/24h and four
seconds after completion stay unchanged. Failed requests remain charged. A
second error pauses permanently under this amendment; reauthorization is a
no-op and cannot clear it. Missing external pins and the old execution commit
refuse before transport. There is no automatic HTTP retry.

`forecast_replay_cache_worker.py authorize-calendar-retry` is offline and needs
the original two pins, new amendment bytes/pin and confirmed execution commit.
`status`, `audit`, `fetch-one` and the bounded full-replay `batch` accept
`--calendar-retry-sha256`. Omitting it after activation refuses. Before using
the code on the real ledger, freeze the reviewed execution commit and amendment
for the exact failure state; do not reuse the unamended controller.

Full export preserves the original acquisition commit and separately requires
`calendar_retry_sha256` and `calendar_retry_execution_commit` in its pinned
scientific protocol. The snapshot discloses both acquisition layers. The
365-day mean correction, minimum 180 pairs, 21 points, six methods, original
twelve development windows and no-promotion limitations do not change.
