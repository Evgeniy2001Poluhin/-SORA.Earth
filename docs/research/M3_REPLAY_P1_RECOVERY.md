# One explicit acquisition recovery — local research only

The original zero-retry acquisition stopped after152/180 training dates on
2024-11-25. Its failed-state bytes, events, quota charge and protocol remain
preserved. That original run is not retroactively declared successful.
The owner authorized continuing the concrete plan: one controlled retry of that
date, then27 dates that have not been requested. No scientific dates, points,
formula, target week, min180 or claim category change.

## Contract

- An independently pinned amendment names the canonical existing ledger, exact
  failed-state hash, new committed execution SHA and exact remaining date set.
- Authorization is offline and uses the existing exclusive lock. It durably saves
  `failed-state-before-recovery.json` and `manual-recovery.json` before changing
  live state. A crash before state publication is locally resumable by exact pins.
- Original events and all original completion receipts must remain byte-equivalent
  as JSON values; the original `code_commit` still records initial execution.
  The amendment separately records the new `execution_commit`.
- Every subsequent worker operation needs the independent amendment pin. A normal
  worker without it refuses. Only one duplicate date is allowed: the named failed
  day, with ordinal2 and amendment SHA. Additional repeats or unknown dates refuse.
- Each new request reserves21 units before transport. The failure remains charged.
  The concrete real totals are at most178newattempts including150old,6210reserved
  units including2472prior. Existing rolling400/4000/7000 and4s spacing remain.
- A second error pauses again. Re-authorizing the same amendment is idempotent and
  **does not** clear a later pause. Wrong pins, another amendment, changed source
  history, pre-existing response, code mismatch and excess attempts refuse.
- A crash with complete previously pinned raw bytes can recover locally with zero
  HTTP. A crash without those bytes pauses; it cannot retry the retry.
- The amended worker stops at the first slice and cannot continue into607dates.

```
python scripts/forecast_replay_cache_worker.py authorize-recovery /canonical/cache \
  --amendment /reviewed/amendment.json --amendment-sha256 EXTERNAL_SHA256 \
  --confirm-code-commit COMMITTED_SHA
python scripts/forecast_replay_cache_worker.py fetch-one /canonical/cache \
  --recovery-sha256 EXTERNAL_SHA256 --confirm-code-commit COMMITTED_SHA
```

These are explicit operator commands; no daemon, cron or automatic retry is
installed. Old acquisition scripts refuse the changed code/authorization. The
science protocol gets an acquisition-revision link and amendment pin; the old
protocol remains immutable. The stage1 export requires that pin to inspect the
settled amended cache. Publication, model promotion, production and live M3 gates
are unaffected. Same-OS-user malicious tampering is outside this local tool's
threat model; pins/locks enforce provenance and accidental drift, not a sandbox.
