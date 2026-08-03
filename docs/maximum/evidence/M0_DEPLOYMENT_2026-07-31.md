# M0 deployment evidence — production

**Captured:** 2026-07-31T21:01:28Z · read-only, with the owner's permission given
in chat. Immutable: this file records what was observed at that moment and is not
updated as things change. Later state belongs in a later file.

## Why this exists

The session's production claims — health, row counts, backup — were stated in
chat and could not be checked by anyone reading GitHub afterwards. That is the
same fault this milestone spent its time removing from the code: a result with no
traceability. The numbers below are recorded with the command that produced them.

## Observed

| | value |
|---|---|
| production git | `531822b` |
| `alembic_version` | `f2c9a1d47b30` |
| `region_esg_scores` | 85 rows |
| `environmental_observations` | 720 rows |
| latest `event_time` | 2026-07-31 20:04:25 UTC |
| `https://sora-earth.online/health` | 200 |
| backup present | `backups/pre_m0_20260731_195141.sql.gz` |

## Commands

```bash
git --no-pager log --oneline -1

docker compose -f docker-compose.prod.yml exec -T postgres \
  psql -U sora -d sora_earth -tAc "SELECT version_num FROM alembic_version"

docker compose -f docker-compose.prod.yml exec -T postgres \
  psql -U sora -d sora_earth -tAc "SELECT count(*) FROM region_esg_scores"

docker compose -f docker-compose.prod.yml exec -T postgres \
  psql -U sora -d sora_earth -tAc "SELECT count(*), max(event_time)
                                     FROM environmental_observations"

curl -s -o /dev/null -w '%{http_code}' https://sora-earth.online/health
```

## The migration that changed data

`e3f8a7c15d92` converged `region_esg_scores` from the legacy shape to the
canonical one. Before, verified read-only prior to running it:

```
region_code   text, and the PRIMARY KEY
env_score, social_score, gov_score, total_score, confidence   real
id            bigint, present but not the primary key
85 rows
```

After:

```
region_code   character varying(10)
PRIMARY KEY   id
85 rows
```

The row count is unchanged. That input shape matched the revision's own docstring
exactly, which is why the conversion was expected to succeed before it was run
rather than hoped to.

## Checks made before deploying, all read-only

| check | result |
|---|---|
| schema vs `Base.metadata` — what `assert_schema_ready()` inspects | 15/15 tables, 198 columns, nullability matches, 0 drift |
| index inventory vs `f2c9a1d47b30`'s frozen expectation | 20 expected / 20 present / 0 missing / 0 extra |
| `region_esg_scores` shape vs `e3f8a7c15d92`'s expected input | exact match |

None of the three found a discrepancy, which is why the deploy proceeded. Had any
of them failed, `alembic upgrade head` or application startup would have refused.

## Backup

Taken before any change, and verified rather than assumed:

```
backups/pre_m0_20260731_195141.sql.gz
1.1 MB · gzip -t OK · 18 CREATE TABLE · region_esg_scores data present
```

**Not verified: restore.** The dump was checked for integrity and content, not
restored into a scratch database. A backup that has never been restored is a
hypothesis. The restore drill run during this milestone was local, against
PostgreSQL 16, never against this dump.

## Gaps open at capture time

**Production is behind `main`.** Production runs `531822b`; `main` is `82c7722`.
The scheduler metrics fix (PR #55) is merged but not deployed, so the six
counters are still `None` on this server and nothing it does is being recorded.

**No backup schedule.** `scripts/backup_*.sh` exist; no cron or timer installs
them. RPO is undefined — not poor, undefined. The dump above was taken by hand
for this deploy and nothing takes another.

**`backend` reports `unhealthy` while serving.** Observed during the deploy: the
container is marked unhealthy while `https://sora-earth.online/health` returns
200. Consistent with issue #50 — the healthcheck does not allow for the time the
ML stack takes to import. Under an orchestrator that acts on the flag, this is a
restart loop waiting to happen.

**`openaq_ingestion`: 333 consecutive runs, 0 records, status `success`.** Cause
unknown; see issue #56. Recorded here as an observation, not a diagnosis.

## What this file does not establish

That the deployment is correct in any broader sense. It records six values, three
pre-flight checks and one backup at one moment. It says nothing about whether the
application behaves correctly under load, whether the data being written is
right, or whether the backup can be restored.
