# Deployment — SORA.Earth Production

## Quick start (single VPS)

```bash
git clone <repo> sora_earth_ai_platform
cd sora_earth_ai_platform
cp .env.example .env
# edit .env — every value .env.example lists without a default; strong
# POSTGRES_PASSWORD, JWT_SECRET, SORA_ADMIN_TOKEN, ADMIN_API_KEY, and the
# SORA_DEFAULT_*_PASSWORD values the first deployment bootstraps accounts from

# Initial deployment, migration and account bootstrap included
./scripts/deploy_production.sh
```

The file is `.env`, beside `docker-compose.prod.yml`. The deployment script
passes no `--env-file`, so compose reads `.env` from the project directory and
nothing else; this section used to create `.env.prod`, which the script never
reads -- the deployment would then have run with every secret unset.

**Do not run `docker compose up` followed by `alembic upgrade head` manually.**
The deployment script handles the migration safely with proper ordering: it
stops the old code before migrating, preventing concurrent access to the
schema during DDL changes. See CLAUDE.md, "Production Server" for details.

Service map:

| Service   | Port | Purpose                               |
|-----------|------|---------------------------------------|
| nginx     | 80/443 | TLS termination + reverse proxy     |
| backend   | 8000 (internal) | FastAPI on gunicorn (4 workers) |
| pgbouncer | 5432 (internal) | Transaction pool, 25 conn       |
| postgres  | 5432 (internal) | Primary DB, persistent volume   |
| redis     | 6379 (internal) | SHAP cache, predictions cache   |

## TLS

Two options:

1. **Caddy / Cloudflare Tunnel** — point at `http://server:80`, get TLS automatically.
2. **Let's Encrypt locally**:
   ```bash
   apt install certbot
   certbot certonly --standalone -d your-domain.com
   cp /etc/letsencrypt/live/your-domain.com/fullchain.pem certs/
   cp /etc/letsencrypt/live/your-domain.com/privkey.pem certs/
   ```
   Then uncomment the HTTPS block in `nginx.conf` and `docker compose restart nginx`.

## Update

**Use the supported deployment script:**
```bash
cd /opt/sora_earth_ai_platform
./scripts/deploy_production.sh
```

The script handles the update safely: stops the old code, runs migrations,
starts the new code with verification. **Do not run `docker compose up` and
`alembic upgrade` manually** — see CLAUDE.md, "Production Server".

### The cutover and its budgets

The order, the image pin and the budgets (`DEPLOY_STOP_TIMEOUT`,
`DEPLOY_DRAIN_SECONDS`, `DEPLOY_MIGRATE_DEADLINE`,
`DEPLOY_MIGRATOR_GONE_SECONDS`, `DEPLOY_MIGRATOR_CREATE_SECONDS`) are described
in CLAUDE.md, "The cutover". The site is down from the stop of nginx until the
new backend is healthy and nginx is recreated. The budgets are upper bounds
chosen before any measurement, not measurements; whether the real window fits
is what the rehearsal below establishes.

### After a refused deployment

Read the exit status and the last lines first; they say what is running.

| exit | journal (`/var/lib/sora/deployments/in-progress`) | what is running |
|---|---|---|
| 1, before the stop | none | what ran before; nothing was touched |
| 1, after the stop | removed | the previous images, restored by id |
| 76 | `rollback-refused` / `rollback-impossible` / `rollback-failed` / `rollback-incomplete` | as printed: `STOPPED` (confirmed), `could NOT be confirmed stopped`, or the new code running |

With a journal left behind, the next run refuses ("a previous run did not
finish"). To recover:

1. Inspect: `cat /var/lib/sora/deployments/in-progress` (state, cutover stage,
   `apps`, `image_pin`) and
   `docker compose -p sora_earth_ai_platform -f docker-compose.prod.yml ps -a`.
2. Read the schema the barriers read: `SELECT version_num FROM alembic_version`
   and whether `evaluations.data_scope` exists. The pending revisions commit
   whole or not at all.
3. Fix forward: merge a corrected release to `main`. Do not start containers by
   hand and do not downgrade the schema.
4. Remove the journal and deploy:
   `rm /var/lib/sora/deployments/in-progress && ./scripts/deploy_production.sh`.
   `--finalize` is only for a run interrupted in state `mutating` after it
   finished everything; it refuses any `rollback-*` journal.

Restoring a backup is a separate, emergency operation (`docs/BACKUP_RESTORE.md`).

### What `--rollback` can and cannot do

- The target must be an ancestor of `origin/main` without `.deploy-blocked`.
- The accounts and ownership barriers refuse a target that cannot run against
  the current schema -- with exit 1, before the checkout, when explicit. Once
  the ownership columns exist, every commit before project ownership is such a
  target, so the older advice below ("roll the application back") no longer
  applies past that release.
- A rollback is a deployment of the older commit: it builds it and runs its
  migrator. Alembic refuses a database at a revision the older code does not
  know, so a target whose migrations stop before the database's current head
  fails at the migrator and the deployment rolls back to what was running.
  An effective `--rollback` target shares the database's current head.

### Rehearsing the cutover

`scripts/rehearse_ownership_cutover.sh` measures the migration, the bootstrap
and the backend's start on a restored production dump, in a PostgreSQL 16 it
creates and removes. Its header lists every refusal; in short:

```bash
# From a separate, clean clone at the release -- never the production checkout.
scripts/rehearse_ownership_cutover.sh \
    --dump /path/to/sora_earth_YYYYMMDD.dump --dump-sha256 <sha256 of it> \
    --image sora_earth_app:current --release-sha <the release's full sha> \
    --pg-image postgres:16-alpine --source-db-bytes <pg_database_size on production> \
    --report /root/rehearsal-<date>.json
```

- Three fresh restores (fewer is never a pass) and a negative run with a
  conflicting lock. Thresholds, fixed in the script: slowest migration ≤ 60 s;
  lock wait ≤ 1 s; DDL lock hold ≤ 60 s; the window -- stop and drain budgets at
  their maxima, the measured migration, bootstrap, schema check and backend
  start to a healthy database, and 30 s for nginx and its checks -- ≤ 300 s;
  free space less the reserve ≥ 3× the sampled peak of database, WAL and
  temporary files.
- Before it starts anything: the image's files match the release, PostgreSQL
  is 16, the report path is new and not the dump, no deployment or backup is
  running, the host load is under `--max-load`, and memory, disk and inodes
  clear 3× the estimate after `--disk-reserve-bytes` (10 GiB by default).
- During: it holds the deployment lock, and aborts (exit 5) if the load stays
  high, memory falls below `--min-mem-kb`, a deployment or backup begins, or
  `--health-url` fails twice.
- It stops itself within `REHEARSAL_WALL_SECONDS` (≤ 3600), cleanup included,
  and exits 3 if it cannot confirm that its containers, network and volumes
  are gone, 4 if the report cannot be written.

`tests/test_rehearse_ownership_cutover.sh` runs it against a synthetic dump
made by `tests/rehearsal_synthetic_dump.sh`. That proves the mechanics -- the
path from `f4b7c2d91e06` through the accounts and ownership revisions, every
refusal, the cleanup -- and nothing about production: a synthetic dump has
production's schema, not its size, its row widths, its index bloat or its
host's disk and load. Only the run on the production dump is evidence for the
thresholds.

### A commit carrying `.deploy-blocked` is not deployable

Production is deployed with `./scripts/deploy_production.sh` (see CLAUDE.md,
"Production Server"); the commands above are for a fresh single host. That
script reads `.deploy-blocked` from the **target** commit -- for a deploy and
for `--rollback` alike -- and refuses before checkout, build, migration or any
container change, quoting the file's first line. There is no override.

The marker is how staged work is merged before it may run. It is present now
because the project-ownership change landed in parts: the schema, the access
rules and the audit are merged, and the cutover that makes them safe to switch
on -- stopping the old backend and scheduler before the migration, and a
rollback barrier for the new schema -- is not. **It is removed by the change
that delivers that cutover, and by nothing else.** Removing it to get a
deployment through deploys the half that was deliberately held back.

The guard lives in the script, so a copy of the script from before it existed
does not read the file.

### Rolling back past `f2c9a1d47b30`

**`alembic downgrade` will refuse.** That revision creates `batch_results`,
`forecast_history`, `region_signals` and `retrain_log` only where they are
absent, so it cannot tell the tables it created from the ones
`Base.metadata.create_all()` had already made — and those hold data. Rather than
move `alembic_version` back over tables that stay behind, it fails and says so.

A release carrying it therefore rolls back **forward or from a backup**, not by
downgrading:

In order of preference:

1. **Roll the application back.** The revision only *adds*, so the previous
   version runs against the new schema unchanged. This is the usual answer and
   needs no schema change at all -- **within the limits above**: once persistent
   accounts or project ownership are in the schema, code from before them is
   refused by the rollback barriers, and the answer past that point is 2.
2. **Forward-fix.** Ship a revision that corrects the problem. A schema that is
   additive-compatible does not need to be unwound to be fixed.
3. **Restore** from a backup taken before the upgrade — see
   `docs/BACKUP_RESTORE.md`. Taking that backup first is what makes this option
   exist.

**Dropping the tables is not on that list, and must not be treated as a rollback
step.** On any deployment that predates the revision they were created by
`Base.metadata.create_all()` and may hold production data the revision never
touched — that is the whole reason `downgrade` refuses rather than dropping them
for you. If removing them is genuinely wanted, it is a separate decision that
starts with establishing provenance and contents:

```bash
# 1. Are they empty? All four, not one.
for t in batch_results forecast_history region_signals retrain_log; do
  psql -c "SELECT '$t' AS table, count(*) FROM public.$t"
done
```

**PostgreSQL provides no built-in record of when a table was created.** An earlier version of this
runbook suggested `pg_stat_get_last_analyze_time`; that reports the last `ANALYZE`
and says nothing about when a table was created or which revision created it.
Provenance can exist -- an event trigger or audit table recording DDL would carry
it -- but only if this deployment set one up. Absent that, it comes from outside
the database: the deployment record or audit log showing which revisions this
database has run. If that record does not exist
for the exact schema in front of you, **restore instead of dropping**.

Only once all four are confirmed empty *and* an external record confirms this
database ran the revision that created them does dropping them and running
`alembic stamp e3f8a7c15d92` become a reasonable act. If either is unknown,
restore instead.

Also: if the revision refuses the *upgrade*, it is reporting that these tables
already exist with a shape that disagrees with the models, and it lists every
difference. That is a real finding about the database, not a fault in the
migration. See issue #51.

## Health checks

- `curl https://your-domain.com/health` → `{"status":"ok"}`
- `docker compose -f docker-compose.prod.yml ps` — all services `healthy`
- `docker compose -f docker-compose.prod.yml logs -f backend`

## Scaling

- `gunicorn -w 4` per container; bump `--scale backend=N` for horizontal.
- `pgbouncer` already pools — safe to add more backend workers.
- Redis is shared cache; no sharding needed at MVP scale.


## Build flow

The SPA is built **inside the image**: `Dockerfile.prod`'s first stage (`spa`,
on `node:22-slim`) runs `npm ci` and `npm run build` with `VITE_API_BASE` from
`docker-compose.prod.yml`, refuses a bundle that contains mock payloads, and
the runtime stage copies `app/static/spa` from it. Nothing is built on the host
first; this section used to say the frontend was built locally and copied in
with `COPY app/ ./app/`, which has not been true since the image became the
source of truth for the SPA.

```bash
./scripts/deploy_production.sh
```

The deployment script builds the image once, pins it by id, stops services
safely, runs migrations, and starts the new code with health verification.

**Do not run `docker compose up` and `alembic upgrade head` manually.** The
script ensures the old code is stopped before the schema changes. See CLAUDE.md,
"Production Server".
