# PostgreSQL backup and restore

How to back up this database, how to bring it back, and the evidence that
bringing it back actually works.

`docs/RUNBOOK.md` covers day-to-day operations; this file covers only backup
and recovery.

---

## Status: restore is proven, backup is not scheduled

A full destroy-and-restore drill passes (numbers below). What does **not**
exist is any automated backup: searching the compose files, the scheduler, CI
and `scripts/` turns up no `pg_dump` on a timer, no `pg_basebackup`, no WAL
archiving. The only mechanism documented anywhere before this file was a
`pg_dump` line in `CLAUDE.md` that someone has to remember to type.

That makes the current recovery point objective **undefined, not merely
poor**. If the database were lost right now, what could be recovered is
whatever dump someone happens to have taken by hand, of unknown age. A fast
restore does not compensate for having nothing to restore from.

Fixing that is a scheduling decision — how much data the project can afford
to lose — and is deliberately left to the owner. Everything needed to
implement it is here: `scripts/pg_backup.sh` is safe to run on a timer, and
its output is exactly what `scripts/pg_restore.sh` consumes.

---

## Drill results

Run on 2026-07-28 against a disposable PostgreSQL 16.14 instance seeded with
synthetic data. Production was not contacted in any way.

| database | logical size | dump size | backup | restore |
|---|---|---|---|---|
| schema + 85 region rows | 8.4 MB | 55.9 kB | 0.25 s | 0.36 s |
| the same + 500 000 prediction rows | 105 MB | 31 MB | 2.54 s | 1.75 s |

Between those two points the dump sustains roughly **42 MB/s** and the restore
roughly **70 MB/s** of logical database size, on a laptop, with the database
in a container. Both drills ended with the restored database
indistinguishable from the original across all 295 fingerprint lines.

### What "restore worked" means here

The drill does not compare dump files — `pg_dump` output is not reproducible
byte-for-byte, so that comparison would fail even on a perfect restore.
Instead it takes a *fingerprint* of the database before the backup and again
after the restore, and requires them to be identical. The fingerprint covers:

- the Alembic revision
- every table and every view
- every column, with type — including declared width and numeric precision, so
  a `varchar(64)` narrowed to `varchar(10)` is visible — nullability and default
- every constraint, with its full definition
- every index, with its full definition
- the row count of every table
- an MD5 of the entire contents of `region_esg_scores`, ordered

It then reads the restored database through the application's own ORM and
through the `regional_esg_snapshot` view, because rows surviving and the
application being able to reach them are two different claims.

### The drill can fail

Verified by damaging a restored database three ways and confirming each was
caught:

| damage | caught by |
|---|---|
| one score value changed by 0.01 in one row | content MD5 |
| `regional_esg_snapshot` dropped | missing view and its columns |
| two rows deleted | row count *and* content MD5 |

---

## Taking a backup

```bash
PG_CONTAINER=postgres ./scripts/pg_backup.sh sora_earth backups/
```

`PG_CONTAINER` names the container holding the database; leave it empty to use
`pg_dump` from `PATH`. It is needed because PostgreSQL's client tools refuse to
dump a server newer than themselves, and because the database normally runs in
a container.

Three files are written to the host, not left inside the container:

| file | purpose |
|---|---|
| `<db>_<utc>.dump` | custom-format dump — the thing you restore from |
| `<db>_<utc>.dump.sha256` | detects a corrupted or truncated dump file |
| `<db>_<utc>.fingerprint` | what the database contained, for verifying a later restore |

Keep all three together. The fingerprint is what turns "the restore finished"
into "the restore was correct".

## Restoring

```bash
PG_CONTAINER=postgres ./scripts/pg_restore.sh backups/sora_earth_20260728T033011Z.dump sora_earth
```

The script checks the dump against its `.sha256` first, then **drops the
target database** and recreates it from nothing. Restoring on top of a
surviving database would hide exactly the failures worth finding.

Stop the application first. `DROP DATABASE ... WITH (FORCE)` will evict live
connections, but an application that reconnects mid-restore will see a
half-populated schema.

To verify afterwards, compare against the fingerprint taken at backup time:

```bash
source scripts/pg_lib.sh
PG_CONTAINER=postgres pg_fingerprint sora_earth > /tmp/after.fingerprint
diff backups/sora_earth_20260728T033011Z.dump.fingerprint /tmp/after.fingerprint
```

Empty output means the restore was faithful.

## Running the drill

Against a disposable instance only — **the drill destroys the database it is
pointed at**:

```bash
source .venv311/bin/activate

docker run -d --name sora-drill-pg -e POSTGRES_PASSWORD=drill \
    -e POSTGRES_DB=sora_drill -p 55436:5432 postgres:16
until docker exec sora-drill-pg pg_isready -U postgres; do sleep 1; done

DATABASE_URL=postgresql://postgres:drill@127.0.0.1:55436/sora_drill \
    python scripts/drill_seed_prod_shape.py

PG_CONTAINER=sora-drill-pg \
DATABASE_URL=postgresql://postgres:drill@127.0.0.1:55436/sora_drill \
    ./scripts/backup_restore_drill.sh sora_drill

docker rm -f sora-drill-pg
```

Pick a port and container name nothing else is using — other drills and the
compose stack take 5432 and the 5543x range. If the virtualenv is not active,
point `PYTHON` at one that has the application's dependencies.

---

## Recovery objectives

**RPO — undefined.** No automated backup exists. Once `pg_backup.sh` runs on
a schedule, the RPO becomes that interval, because this is dump-based backup
with no WAL archiving: everything written since the last dump is lost. Getting
below that means continuous archiving, which is a larger change than this.

**RTO — dominated by everything except the restore.** The measured restore of
a 105 MB database is under two seconds. A real recovery is noticing the
problem, deciding to restore, finding a dump, stopping the application,
restoring, verifying the fingerprint, and starting back up. Budget the restore
itself from the throughput above and treat the rest as the real cost.

---

## What this evidence does not cover

- **Production was never touched.** No connection, no `pg_dump`, no read. The
  drill reproduces production's *shape* from the description in
  `HANDOFF_2026-07-27.md` §4a — `region_code text` as primary key, five `real`
  score columns, `id bigint` that is not the key — but the data is synthetic
  and the row counts of every table other than `region_esg_scores` are not
  production's.
- **The timings are from a laptop container**, not from the production host,
  its disks, or its network. Use the throughput figures to extrapolate, then
  measure on the real host before quoting an RTO to anyone.
- **`id` is seeded as `GENERATED BY DEFAULT AS IDENTITY`.** Production's exact
  identity flavour was not re-read for this drill. If it differs, the restore
  path is unaffected — the property being tested is that whatever shape exists
  survives the round trip — but the seeded fixture would differ from
  production in that one detail.
- **No restore was tested onto a different PostgreSQL major version**, or onto
  a host with different locale or extension availability. Both are real
  recovery scenarios and neither is covered here.
