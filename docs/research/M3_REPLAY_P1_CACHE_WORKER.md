# P1 forecast cache worker — local candidate, no acquisition scheduled

This implements cache mechanics only. No real HTTP, real-data calibration,
quality evaluation, service installation or production action was performed in
its implementation/test scope. The existing three probes and P0 are unchanged.
Independent code/design review and a frozen execution protocol still precede
mass acquisition. No script silently starts a background loop.

## Interface and execution provenance

`scripts/forecast_replay_cache_worker.py` has five explicit commands:

| Command | Behavior |
| --- | --- |
| `init` | Create a NEW private cache/ledger, given an independent plan SHA256 and explicit prior reservation. Never overwrite an existing directory. No HTTP. |
| `import-one` | Verify an independent source manifest pin, exact request parameters and raw-byte pin; validate target grid, then import one response without HTTP. |
| `status` | Read local journal and verify completed byte pins; no HTTP. |
| `fetch-one` | At most one allowlisted public forecast GET, requiring exact current/initialized code commit. No implicit retry, sleep or daemon. |
| `audit` | Verify pins and parse every completed target grid. Complete cache means all607 declared requests; no model/skill claim. No HTTP. |

`init` and `fetch-one` require clean committed sources. `fetch-one` additionally
requires `--confirm-code-commit` matching HEAD AND initialized ledger. A new
commit is not silently accepted; keep the original reviewed checkout or design
a separately reviewed journal-preserving upgrade. Never reset quotas to upgrade.

The plan is required to equal the reviewed deterministic calendar plan, not an
arbitrary list of URLs. All21 points, IFS00UTC,8 forecast days,7 complete UTC
targets,24 valid hours each; a valid unused initialization day may also exist.
Existing11 P0 reuses and3 probe dates must be imported before new HTTP, avoiding
accidental redownload. Import manifests need an independently supplied pin,
not a digest automatically calculated as its own trust anchor.

## Quota semantics

One canonical shared ledger must cover every invocation of this experiment.
Reserve21 weighted units durably **before** transport. Hold a nonblocking Unix
process lock across reading state, checking quota, reserving, receiving and
recording completion. A competing process refuses; it does not queue a request.

Persist attempts through atomic state replacement: temporary file in the cache
directory, flush+fsync, os.replace, directory fsync. fsync errors propagate;
there is no assumed durability on error. Original responses are exclusive new
files, flushed/fsynced with their directory, never silently overwritten.

Conservative windows:400 weighted units/rolling60s,4000/rolling3600s,
7000/rolling24h (8000 internal daily cap minus1000 other-traffic allowance).
This is deliberately stronger than assuming a midnight reset. The earlier
calendar allotments218/333/45 are planning arithmetic, NOT this worker's timing
guarantee. Unknown provider accounting/shared-IP usage is not authenticated by
the ledger; higher external usage invalidates the allowance and requires a stop.

`init` seeds all supplied prior units at initialization time, even if actually
older; that conservatively occupies minute/hour/24h windows longer. The latest
known prior own-weather reservation is2472 onUTC22.10, NOT the obsolete2299.5.
Recalculate before a future execution; do not copy the figure after new traffic.

When blocked, return `quota_wait` and `not_before`, with no request or sleep.
On success, retain reservation start and charge rolling windows until response
completion; the next start is at least4s after that accounting timestamp.
Backward clocks refuse. An HTTP429 is a failed spent attempt and persistent
pause, unlike a normal quota wait. No automatic retry or reset command exists.

The actual transport is a single isolated Python child with a sanitized
environment, one public allowlisted GET,40s socket timeout and45s hard process
deadline. Output <=256KiB; redirects/proxies/credentials are not used. Timeout
spends the reservation and pauses. Error output contains class/status only,
not provider payloads, credentials, arbitrary exception messages or paths.

## Crash and corruption behavior

- Reservation exists but no saved raw response: outcome uncertain; pause for
  review. Do not assume the provider never received the request.
- Response received: persist its SHA256 before writing raw bytes. Crash after
  durable raw save: re-read that file, require the already-persisted hash and
  full grid validation, and finish the receipt without HTTP.
- Partial or changed raw file: refuse/pause. Never reconstruct missing weather
  values, fill dates or download a replacement behind the same receipt.
- Imports also journal their source-manifest and raw pins before copying; saved
  pinned imports can be recovered locally after a crash.
- Every resumed invocation verifies completed raw pins. Unreceipted target files,
  symlink roots/raw/state/inputs and source filename traversal refuse. Temporary
  files and partials are left for review; no cleanup/deletion operation exists.

`ledger_at` is a sampled local journal clock, not evidence of historical provider
publication. Source and content pins prove consistency with supplied trusted
bytes; they do not authenticate old model cycles or original label availability.

## Boundaries

Unix/macOS/Linux only. Lock/state guards coordinate this worker; they are not
an OS sandbox against the same user modifying state or using another ledger.
Keep one canonical ledger path in the eventual frozen execution protocol and
account for all other clients. No contact with old private archives, keys,
IEG/R0/R1/R1a or production is part of this work.

The journal remains mutable acquisition state. A successful audit is readiness
for independent review, not an immutable published research snapshot. Final
export/pinning and the separate P1 evaluation protocol are still required before
any fit/scoring. No success claim, deployment or change to live M3 follows.
