# Deployment — SORA.Earth Production

## Quick start (single VPS)

```bash
git clone <repo> sora_earth_ai_platform
cd sora_earth_ai_platform
cp .env.prod.example .env.prod
# edit .env.prod — set strong POSTGRES_PASSWORD, JWT_SECRET, ADMIN_API_KEY

docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

Initial DB migration:

```bash
docker compose -f docker-compose.prod.yml exec backend alembic upgrade head
```

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
```bash
./scripts/deploy_production.sh
```

### A commit carrying `.deploy-blocked` is not deployable

Production is deployed with `./scripts/deploy_production.sh` (see CLAUDE.md,
"Production Server"). Do not run the application and then migrate underneath
it. The script reads `.deploy-blocked` from the **target** commit -- for a deploy and
for `--rollback` alike -- and refuses before checkout, build, migration or any
container change, quoting the file's first line. There is no override.

The marker is how staged work is merged before it may run. It is present now
because the project-ownership change landed in parts: the schema, the access
rules and the audit are merged. The local cutover implementation now stops and
verifies the old writers and persists the schema rollback barrier. The marker
remains until the populated-copy migration rehearsal has established migration
time and lock behaviour and the operator has checked the provenance of existing
rows. Neither rehearsal nor production migration has been performed by this
change. Removing the marker merely to pass the guard is forbidden.

The guard lives in the script, so a copy of the script from before it existed
does not read the file.

### Ownership cutover and recovery

The target must carry `.ownership-cutover-v1` with the value
`ownership-cutover-v1` to declare support for this cutover. After building the
image and waiting for PostgreSQL, the deployer stops backend and scheduler,
checks that no running container with either service label remains in this
Compose project, and persists `ownership-cutover-required` in `MANIFEST_DIR`
(default `/var/lib/sora/deployments`). The file and its directory are synced
before any ownership migration can run. It is not removed after success.

The first application start forces backend and scheduler to be recreated on
the new image: a plain `up` could briefly restart the stopped old containers.
If migration or a later step fails, the deployer requests application stop,
retains the journal and barrier, and exits 76. It does not automatically restore
old images or undo DDL. Inspect the actual schema and fix forward. A failed
stop is reported in the rollback log; it is not proof that the service stopped.

Explicit rollback to a reviewed compatible commit is still possible. A target
without the capability is refused before checkout if the durable barrier exists,
if ownership columns/audit tables exist in PostgreSQL, or if that database check
cannot be read. This protects manually migrated databases as well as scripted
cutovers. Never delete the barrier or run the ownership downgrade to make old
code fit: that would lose ownership and audit history.

An old copy of the deployment script and manual Docker commands do not enforce
these controls. The operator must use this version of the deployer for the
initial cutover and subsequent recovery; the marker is not a host-wide execution
policy. A populated restored-copy rehearsal is still required before lifting
`.deploy-blocked`; synthetic tests do not measure real table update/index cost.

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
   needs no schema change at all.
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

Frontend is built locally before `docker compose build` to avoid native-deps
issues with Vite/Rolldown inside Alpine containers and keep the final image slim.

```bash
cd web && npm ci && npm run build && cd ..
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec backend alembic upgrade head
curl http://localhost/health
```

The SPA lands in `app/static/spa/` and is copied via `COPY app/ ./app/`.
