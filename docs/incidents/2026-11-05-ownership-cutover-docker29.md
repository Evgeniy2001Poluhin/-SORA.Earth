# Incident: the first ownership cutover stopped on Docker 29's lower-case "no such object"

**Date:** 2026-11-05
**Severity:** P1. The public site did not serve.
**User-facing downtime:** about 77 minutes. The estimate in
`docs/OWNERSHIP_CUTOVER_RELEASE.md` was about 133 s.
**Data:** none lost. The migration committed whole. The three public rows in
`evaluations` and the three in `predictions_log` are intact.

## TL;DR

The first run of `scripts/deploy_production.sh` for project ownership
stopped the old backend and scheduler, and it ran the migration
(`f4b7c2d91e06 -> a1c5e9f3b7d2 -> b7d41f2e9c35`), which committed. It then had
to confirm that the migrator container was gone.

Docker 29.9 on the host answers `error: no such object: <id>`. The script
matched only `No such`, so it read the confirmed removal as a daemon that
could not answer. After 30 s it refused. It also refused the automatic
rollback on the same check, and left backend, scheduler and nginx stopped
(exit 76, journal `rollback-refused`).

Nothing was started by hand. The fix was merged through a pull request with
green CI, and the deployment was run again with the script. That second run
finished with exit 0.

## Timeline (UTC, 2026-11-05)

The deployment log has no timestamps. The times below come from Prometheus
`up{job="sora-app"}` (15 s resolution), the journal, the run's exit file and
the containers' start times.

| time | event |
|---|---|
| 10:08 to 10:12 | All seven GO conditions checked. Fresh baseline taken; the corpus is still 3 + 3 rows. GPT gave GO for `d35c102f`, and the owner confirmed. |
| 10:12:46 | First run started. The checkout was fast-forwarded first, and the blob of `scripts/deploy_production.sh` was verified. |
| ~10:15 | The image was built (113.6 s) and pinned by id (`sha256:e870d68f04f8…`). |
| ~10:18 | nginx stopped, then backend and scheduler. Open transactions drained. The migrator ran and committed both revisions. |
| 10:18:15 | `up{job="sora-app"}` reached 0, the first scrape without the backend. |
| 10:18:51 | Exit 76. The journal reads `rollback-refused`, with detail "the migrator could not be confirmed stopped; the schema was not read". |
| 10:19 to 10:35 | Diagnosis. `alembic_version` read `b7d41f2e9c35`, `docker ps -a` showed no migrator, and `docker inspect` printed the lower-case message. The cause was found at `scripts/deploy_production.sh:639`. GPT allowed fix-forward. |
| 10:35 to 11:23 | The fix was written and tested: 650/650 in `debian:bookworm`, and the new case fails with RC 76 on the old script. The first CI attempt hit `No space left on device` on the runner, and the rerun was green. The fix was merged, giving `main` = `75c29bc3`. |
| 11:30:17 | The journal was saved to `/root/journal-20261105T101246Z-601387.rollback-refused`. With no deploy process and no migrator running, alembic at head and no other sessions, `in-progress` was removed and the checkout fast-forwarded. The second run started. |
| 11:35:28 | nginx started. |
| 11:35:45 | `up{job="sora-app"}` was back to 1. |
| 11:35:47 | Exit 0. Manifest `/var/lib/sora/deployments/20261105T113546Z-yO1D4M.txt`. |

## Root cause

```bash
# scripts/deploy_production.sh, before 75c29bc3
case "$gone" in
    *"No such"*) break ;;
```

Docker changed the capitalisation of the message. The guard treated any
other text as "the daemon cannot say", which is correct in itself: an
unanswered question is not "gone". The only problem was that it did not
recognise the new spelling of the positive answer.

## Why CI and the rehearsal did not catch it

- **The test stub spoke an older Docker.** `tests/test_deploy_production.sh`
  stubs `docker inspect` and printed `Error: No such object`. The suite proved
  the logic against a message production no longer sends.
- **The rehearsal does not take this path.**
  `scripts/rehearse_ownership_cutover.sh` runs its migrator itself and never
  calls the deploy script's removal check. Docker itself is identical on the
  host, but the code that read its message was not exercised there.
- **CI runners use a different Docker.** No job runs the deploy script
  against a real daemon.

## Why the outage was about 77 minutes and not about 133 s

The 133 s figure was an upper estimate for a run that succeeds. Once the run
refused, recovery had to go through the only path the rules allow:

- write and test the fix (about 45 minutes);
- wait for CI, including one infrastructure failure on the runner and its
  rerun (about 35 minutes);
- deploy again (5 minutes).

The barriers worked as designed: neither the old code nor an unverified new
start ran onto a schema the script could not vouch for. The cost of that
design is that a defect in the script itself is fixed at CI speed.

## What changed

- `75c29bc3` (fix-forward): the script accepts both spellings, and a deploy
  case makes the stub print Docker 29's form and requires the deployment to
  succeed.
- This change: the success record no longer suggests `--rollback <previous>`
  when a barrier would refuse it. After this release it suggested exactly
  that, for `481264b6`.
- Audit: the `case` in the migrator-removal check is the only place in
  `scripts/*.sh` that parses the text of a Docker answer. Every other Docker check uses exit codes or ids, so no
  other spelling-dependent check exists.

## What would shorten the next one

- Keep test stubs in step with the Docker version on the host. A case that
  prints the current host's messages is cheaper than an outage.
- Look first for any guard that parses a message text, before relying on a
  rehearsal that does not exercise the deploy script's own checks.

## Records

| what | where |
|---|---|
| first run, log | `/root/deploy-20261105-d35c102f.log` (and `.keep`) on the host |
| first run, journal (`rollback-refused`) | `/root/journal-20261105T101246Z-601387.rollback-refused` |
| first run, rollback log | `/var/lib/sora/deployments/rollback-20261105T101246Z-601387.log` (empty) |
| second run, log | `/root/deploy-20261105-75c29bc3.log` |
| second run, manifest | `/var/lib/sora/deployments/20261105T113546Z-yO1D4M.txt` |
| fix | merge `75c29bc3`, commit `e0dbe9d2` |
| release record | `docs/OWNERSHIP_CUTOVER_RELEASE.md` |
