# One transport retry after calendar recovery

This separate amendment permits ONE new attempt on the pinned failed date.
It never resumes the remaining calendar. No scientific definitions or rolling
limits change. The human authorization for the actual run is 2025-05-05 only.

The old calendar checkpoint, policy and failed streaming body remain intact.
The current failed state must be independently pinned, have no response file,
and record TransportFailure / URLError / child returncode2 on its final attempt.
The new saved checkpoint is `failed-transport-state.json`; the new immutable
policy is `transport-retry.json`. Policy bytes, all three parent pins and the
committed execution source are explicit CLI inputs. Nothing authorizes itself.

Offline authorization preserves quota events, receipts and all parent identities.
Only the reviewed pause/inflight are cleared, and a distinct authorization marker
and effective policy pin are written. Original workers then refuse the ledger.
Crashes before state publication can resume authorization without making a GET.
Reauthorization of an already active policy never clears another failure.

The total execution ceiling is prior attempts+1 and prior reserved units+21.
The failed attempt stays charged, with its original timing. Rolling caps remain
400/minute,4000/hour,7000/24h and four seconds after response completion. Quota
wait consumes no attempt. Exactly one new event must match the failed date with
ordinal2 and the new amendment pin. A new failure stops permanently under this
authorization; a crash with uncertain transport cannot trigger another request.

`scripts/forecast_replay_transport_retry.py` exposes `authorize`, `fetch-one`
and read-only `status`. Source must be clean and match the explicit commit.
`fetch-one` makes at most one bounded transport call. After success later calls
return `authorized_transport_retry_complete_stop` without network requests.
No daemon, automatic retry, fitting, evaluation or export is provided.

After this one date is resolved, the old full-calendar tool remains incompatible
with the changed effective policy. Continuing other dates requires a separately
reviewed transition that preserves this retry and its quota/provenance, not
resetting state or silently returning to the former worker. The scientific
snapshot/export protocol must disclose the extra retry before any future export.
This code is acquisition bookkeeping, not evidence that live M3 or model skill
passed. Synthetic tests do not attest actual provider availability.
