# Collection after a completed one-shot transport retry

This is an acquisition-only scope extension, not another retry. Its independently
pinned policy and immutable settled checkpoint chain all four previous policies.
Only the exact ordered missing dates, disjoint from every previous attempt, are
allowed. There is no import, retry, quota reset, failure clearing or model fit.
A checkpoint with an incomplete/paused/inflight one-shot retry is refused.

The original event prefix and completion receipts remain unchanged. New events
retain the original calendar provenance pin; the effective continuation marker
and checkpoint boundary identify this additional acquisition source. New events
cannot carry retry ordinals or retry amendment labels. Every completion must
match its reserved date, source and response time; a crash may recover only saved
hash-pinned bytes, without HTTP. Any new failure stops the worker. Reauthorizing
the same policy is idempotent and does not clear that failure.

`forecast_replay_post_retry.py` exposes explicit offline `authorize`, `status`,
and `audit`, and a single `batch` bounded to 40 requests / 600 seconds. All five
external policy pins and the exact clean execution commit are required. The
shared controller/worker locks, 400/minute, 4000/hour, 7000/day rolling units and
four-second spacing after each response remain unchanged. Each attempt costs
21 units including failure. Quota waits return without another request. There
is no daemon, automatic retry, or hidden long quota sleep. Older workers reject
the changed effective policy pin.

The reviewed live plan has 406 completed dates and 201 never-attempted dates;
ceilings are 596 total attempts and 14988 total reserved units, counting all
historical attempts, failures and prior allowance. These counts are derived
from the independently pinned checkpoint, not assumed by the implementation.

Do not use the old export/evaluation CLI on the amended ledger. Export remains
fail-closed until a separately reviewed protocol discloses the new acquisition
source and all amendments. Complete 607-date semantic audit comes before any
full-calendar evaluation. Historical replay does not replace the M3 live clock.
