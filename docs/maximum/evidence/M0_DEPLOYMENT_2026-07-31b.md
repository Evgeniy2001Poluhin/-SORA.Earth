# M0 deployment evidence — production, second capture

**Captured:** 2026-07-31T21:12Z · read-only verification after a deploy, with the
owner's permission given in chat.

Follows `M0_DEPLOYMENT_2026-07-31.md`, which is immutable and records the state
before this. Later state belongs in a later file — amending the first one would
destroy the property it exists for.

## What changed and why

The first capture found production on `531822b` while `main` was `82c7722`: PR
#55 was merged but not deployed, so the six scheduler counters were still `None`
on that server and nothing the scheduler did was being recorded. The fix existed
in the repository and not in production, and those are different claims.

The diff between the two commits is `app/scheduler.py` and its test — **no
migrations**, so this needed a container rebuild and nothing else. No backup was
taken because no schema or data is touched by it.

## Observed after

| | value |
|---|---|
| production git | `82c7722` |
| scheduler container | `Up (healthy)` |
| counters that are `None` | 0 of 6 |
| `app.main` in `sys.modules` after importing the scheduler | False |

## Commands

```bash
git pull --ff-only
docker compose -f docker-compose.prod.yml up -d --build scheduler

docker compose -f docker-compose.prod.yml exec -T scheduler python -c "
import app.scheduler as s, sys
names = ['sora_retrain_total','sora_refresh_total','sora_full_pipeline_total',
         'sora_drift_detected_total','sora_model_promoted_total',
         'sora_model_rejected_total']
print('None_counters=' + str(len([n for n in names if getattr(s, n, None) is None])))
print('app_main_loaded=' + str('app.main' in sys.modules))
"
```

## What this establishes, and what it does not

**Established:** the six counters are real objects in the running scheduler
process, and importing that scheduler no longer drags in `app.main` with torch
and SHAP behind it.

**Not established:** that anything has been counted yet. The counters existing is
not the same as values being recorded — the jobs that increment them run hourly,
and none has fired since the rebuild. Nor is it established that the scheduler's
metrics are scraped: it is a separate process from the backend, and whether
Prometheus reaches its registry was not checked.

The honest statement is that the mechanism is in place. Whether the numbers
arrive is a separate observation, and the first job run after
2026-07-31T21:12Z is what will show it.

## Still open, unchanged from the first capture

- no backup schedule; RPO undefined
- the existing dump has never been restored
- `backend` reports `unhealthy` while serving (#50)
- `openaq_ingestion`: 333 runs, 0 records, status `success`, cause unknown (#56)
