# Incident: two failed deployments of #262, and one backend restart I caused

**Date:** 2026-09-06
**Severity:** P3 — no user-facing outage; one unplanned container restart
**User-facing downtime:** none measured. `/health` answered 200 throughout, and
the two failed deployments rolled back before serving any traffic on the new
image.

## TL;DR

Deploying the Prometheus multiprocess change refused twice and rolled back on
its own, both times for a real reason. Separately, my own verification probe
sent `SIGTERM` to the gunicorn **master** instead of a worker, restarting the
backend container. Nothing was lost; the numbers I read immediately afterwards
were wrong because of it, and I reported them as a possible failure of the
change before finding my own mistake.

## Timeline (UTC, 2026-09-06)

| Time  | Event |
|-------|-------|
| 18:43 | Deploy of `174e305d`. Image builds, containers start, `/health` returns 502 five times. |
| 18:44 | The script refuses and rolls back to `e8538e92`. Records `rollback-incomplete`: it rebuilt the previous source rather than restoring the recorded image ids. |
| 18:45 | Verified from outside: `/health` 200 in 0.53s, nine containers up, backend healthy. **Production never stopped serving.** |
| 18:45 | Cause reproduced on the host: a tmpfs declared `mode=0700` is created root-owned; the service runs as `1000:1000`; `entrypoint.sh`'s writability check refused to start. |
| 19:02 | Fix merged (`6b59b4c`): `uid=1000,gid=1000` on the mount, plus a test comparing it with the service's `user:`. |
| 19:04 | Deploy refuses: an unfinished run is recorded. Correct — a deployment must not run over an unfinished one. |
| 19:05 | The in-progress record is archived, not deleted, as `20260906T184335Z-FAILED-174e305d-tmpfs-not-writable.txt`. |
| 19:05 | Deploy refuses again: the checkout is on a detached HEAD. The 18:44 rollback had checked out `e8538e92` detached, and my `merge --ff-only` moved that instead of `main`. |
| 19:06 | Checkout returned to `main`; deploy succeeds. |
| 19:07 | Verification probe kills pid 7 believing it a worker. It is the gunicorn master. The backend container restarts. |
| 19:08 | Probe rewritten to identify the master by parent pid. Re-run: 40/40 aggregated delta, five identical scrapes, worker kill leaves the total unchanged. |

## Root causes

**1. tmpfs ownership.** `tmpfs: - /path:mode=0700,size=16m` creates the mount
owned by root. Measured, not reasoned about:

```
docker run --user 1000:1000 --tmpfs /tmp/probe:mode=0700,size=8m alpine \
  sh -c 'ls -ld /tmp/probe; touch /tmp/probe/x'
drwx------  2 root root  /tmp/probe
touch: /tmp/probe/x: Permission denied
```

**2. My probe killed the master.** It excluded pid 1 and took the last
remaining gunicorn process. pid 1 is `tini`; the gunicorn master was pid 7, and
the workers were 11–14.

## What worked

- **Fail-closed at startup.** `entrypoint.sh` refuses when the directory is not
  writable. Without it the container would have started, reported healthy, and
  raised inside every request handler that touches a metric — a half-broken
  service nobody would have attributed to metrics.
- **The deploy script's own gates.** It checked `/health` from outside, refused,
  rolled back, refused to run over an unfinished record, and refused to deploy
  from a detached HEAD. Three separate refusals, each correct.

## What was missing

Nothing checked the tmpfs declaration itself. `tests/test_image_contents.py`
had caught `gunicorn_conf.py` not being copied into the image; no test looked at
who owns the mount. Added: `test_the_tmpfs_is_owned_by_the_user_the_container_runs_as`
reads `uid`/`gid` from the mount and `user:` from the same compose file and
requires them to match.

## Follow-ups

- Multiprocess files accumulate per dead worker: measured ~24 KiB each, tmpfs is
  16 MiB and 1% used, so roughly 680 worker deaths of headroom. Not opened as an
  issue; revisit if worker churn becomes routine or the usage grows.
- Counter, histogram and summary files for dead pids **must not** be swept.
  Removing them makes a monotonic counter go backwards.
