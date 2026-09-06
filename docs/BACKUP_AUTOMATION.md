# Scheduled backups

Extends the manual tooling in `docs/BACKUP_RESTORE.md`. That document proves a
dump can be restored; this one is about doing it unattended, off the host, and
in a form the host itself cannot read.

## What runs

```
lock → dump → compress → encrypt → checksum → upload → verify
     → publish manifest → retention → alert on failure
```

Every stage fails closed. `scripts/backup_run.sh <database>` is the whole job.

## The completion contract

S3 has no atomic rename, so the local trick — write a temporary object, then
move it into place — does not exist. A reader can always catch a multi-object
upload half finished, and a half-finished payload that looks like a backup is
worse than no backup: you find out during an incident.

So completion is explicit. Every object is written first and a small
`manifest.json` is written **last**.

**Nothing without a manifest is a backup.** Restore refuses it, retention
ignores it, and it cannot take a weekly slot. Debris is swept separately, after
a grace period, so an upload still in flight is never mistaken for it.

## Encryption

The backup host must not be able to read its own backups. A passphrase fails
that: whatever encrypts also decrypts, so compromising the machine that runs the
schedule hands over the archive with it.

A **versioned OpenSSL envelope with encrypt-then-MAC**. The shape is the same
one `age` and GPG use — a sealed data key over a symmetric payload — but this is
not that format and has had none of its review. It is narrower, and the header
exists so the algorithms can change later without a reader guessing.

| object | contents |
|---|---|
| `payload.hdr` | envelope version and algorithm identifiers, in the clear |
| `payload.enc` | AES-256-CBC under a per-backup data key |
| `payload.mac` | HMAC-SHA256 over **header ‖ ciphertext** |
| `payload.key` | the data key, IV and MAC key, sealed to an RSA public key with OAEP |

The keys are independent, not one key reused: 80 random bytes per backup, split
into a 32-byte AES key, a 32-byte HMAC key and a 16-byte IV. Using one key for
both confidentiality and authentication weakens each.

The header is authenticated **together with** the ciphertext. Signing only the
ciphertext would leave the parameters describing it unprotected, which is how
downgrade attacks on otherwise sound constructions work. An unrecognised version
is refused before the payload is touched at all.

Built on OpenSSL rather than `age` or GPG, neither of which is present in this
environment, and a script that needs a package nobody installed is a schedule
that never runs.

The RSA seal is OpenSSL. The **symmetric half — AES-256-CBC and the HMAC — runs
in `scripts/backup_crypt.py`**, because `openssl enc` accepts a raw key only as
`-K <hex>` and `openssl dgst` its MAC key only as `-macopt hexkey:<hex>`. Both
land in `/proc/<pid>/cmdline`, which is mode 444: measured on Linux, an
unprivileged user read the AES key out of it while root was encrypting. Every
OpenSSL option that keeps a secret off the argument list feeds a passphrase
through a KDF instead, which would be a different envelope — so the only way to
keep both the format and the guarantee was to stop using the CLI for that step.
The helper reads the key material from a file the caller creates `0600`; only the
path reaches `argv`, and a path is not a secret.

The wire format did not change when that moved. A test encrypts with the helper
and decrypts with `openssl enc`, and the reverse, and compares the ciphertexts
byte for byte — which is why the version field is still `/1`.

The helper needs `cryptography`, which is already in `requirements.txt`. Point
`BACKUP_PYTHON` at the interpreter that has the application's dependencies. It is
checked once at the start of encrypt and decrypt, and refuses with instructions
rather than discovering it at 03:00.

Authentication happens **before** decryption: a modified payload is refused, not
decrypted and then judged.

Generating the pair — the private half never reaches the backup host:

```bash
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out identity.pem
openssl pkey -in identity.pem -pubout -out recipient.pem
```

## Installing the schedule

The scripts do not run themselves. Until the timer below is enabled there is no
backup, however green the tests are — this was GAP-007's `PARTIAL` standing.

They run **on the host**, not in an image: `pg_lib.sh` reaches the database with
`docker exec "$PG_CONTAINER"`, because PostgreSQL's client tools refuse to dump a
server newer than themselves. That is why no Dockerfile ships `scripts/`.

```bash
sudo install -m 0644 infra/systemd/sora-backup.service /etc/systemd/system/
sudo install -m 0644 infra/systemd/sora-backup.timer   /etc/systemd/system/
sudo install -d -m 0700 -o sora -g sora /etc/sora-earth
# Created only when absent. `install /dev/null` truncates the destination, so
# running this a second time -- following the same documented instructions --
# erases the S3 credentials and every setting in the file, and the next backup
# fails with no obvious connection to what was done.
sudo test -e /etc/sora-earth/backup.env \
  || sudo install -m 0600 -o sora -g sora /dev/null /etc/sora-earth/backup.env
# then write BACKUP_RECIPIENT_KEY, BACKUP_S3_* and PG_CONTAINER into that file
sudo systemctl daemon-reload
sudo systemctl enable --now sora-backup.timer
```

Two settings in the unit are not decoration:

`RuntimeDirectory=sora-earth` with `RuntimeDirectoryMode=0700` is what
`backup_lock.sh` demands in production — it refuses to guess a lock directory,
and its error message names these two lines. systemd creates `/run/sora-earth`
owned by the service user on start and removes it on stop.

`SuccessExitStatus=75` matters more than it looks. `backup_run.sh` exits 75
(EX_TEMPFAIL) when another run already holds the lock. Without this line systemd
records a skipped run as a failed unit, and a benign overlap pages somebody — the
same outcome the `LOCK_SKIP` flag prevents one layer down.

## When it stops running

`backup_run.sh` alerts when a backup **fails**. Nothing alerted when one stops
**happening** -- a disabled timer, a unit never enabled after a rebuild, a host
restored without its schedule. That failure is silent by construction: no job
runs, so no job reports, and it is discovered during a restore.

    sudo install -m 0755 scripts/backup_age_check.sh /opt/sora_earth_ai_platform/scripts/
    sudo install -m 0644 infra/systemd/sora-backup-age.service /etc/systemd/system/
    sudo install -m 0644 infra/systemd/sora-backup-age.timer   /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now sora-backup-age.timer

It runs at 07:30 UTC, four hours after the dump, so a slow run has finished and
only a run that did not happen is reported. The unit failing **is** the alert:
`systemctl --failed` shows it without any delivery configured. Set
`BACKUP_ALERT_HOOK` when there is somewhere to send it.

An empty directory fails, and this is the case the check exists for: "the newest
dump is older than N hours" over zero dumps has no newest, and the obvious
spelling of it compares an empty string and reports healthy. The count is
checked before the age and printed either way.

Checking that it is actually running, which is the M0 criterion:

```bash
systemctl list-timers sora-backup.timer
journalctl -u sora-backup.service --since '2 days ago'
./scripts/backup_retention.sh          # lists what is in the object store
```

## Configuration

| variable | meaning |
|---|---|
| `BACKUP_RECIPIENT_KEY` | public key; the only key the backup host needs |
| `BACKUP_IDENTITY_KEY` | private key; **restore side only** |
| `BACKUP_S3_ENDPOINT` | any S3-compatible endpoint |
| `BACKUP_S3_BUCKET`, `BACKUP_S3_PREFIX`, `BACKUP_S3_REGION` | object location |
| `BACKUP_S3_ACCESS_KEY_FILE`, `BACKUP_S3_SECRET_KEY_FILE` | credentials, read from files |
| `BACKUP_S3_CLIENT` | client executable, default `aws`; a test double at `tests/fakes/fake_s3` |
| `BACKUP_KEEP_ROLLING`, `BACKUP_KEEP_WEEKLY` | retention, default 28 and 8 |
| `BACKUP_ALERT_HOOK` | executable called on failure |
| `BACKUP_PYTHON` | interpreter for `backup_crypt.py`, default `python3`; needs `cryptography` |
| `BACKUP_RUNTIME_DIR` | `0700` directory owned by the service user, for the lock; **required in production** |

Credentials are read from files and exported to the client, never passed as
arguments — an argument list is readable by every process on the host. That rule
applies to the envelope's keys too; see above for why that meant leaving the
OpenSSL CLI for one step.

## Retention

- the newest **28** completed backups
- the newest completed backup of each of the last **8** ISO weeks
- one backup can be both; the keep-set is a union
- the whole keep-set is computed **before** anything is deleted
- the newest completed backup is never deleted, whatever the settings say
- a retention failure alerts but does not invalidate the backup that just ran

Dry run by default. `BACKUP_RETENTION_APPLY=1` to act.

## Restore

```bash
BACKUP_RESTORE_TARGET=sora_drill \
BACKUP_IDENTITY_KEY=identity.pem \
  ./scripts/backup_restore.sh <backup-id>
```

Both the backup and the target are explicit and neither has a default.
`sora_earth` and `postgres` are refused outright — a production restore is a
decision with a rollback plan, not a command. The payload is checked against the
manifest before anything touches PostgreSQL, and no plaintext survives the run.

## Promotion is not an atomic swap

The restore *construction* is atomic — `--single-transaction`, into a staging
database, discarded on any failure. Taking the name afterwards is not, and
calling it "promotion" should not suggest otherwise:

```
DROP DATABASE target
ALTER DATABASE staging RENAME TO target
```

Two statements, not one. Between them the target does not exist, and a process
that dies in the gap leaves it that way. Both statements also fail outright if
anyone is connected — measured:

```
ALTER DATABASE stg RENAME TO tgt
ERROR:  database "stg" is being accessed by other users
DETAIL:  There is 1 other session using the database.
```

So a running application defeats the swap rather than surviving it, and a
connection pool that reconnects during the gap will find nothing there.

**This script therefore refuses production.** `sora_earth` and `postgres` are on
`BACKUP_PROTECTED_DATABASES` and the restore exits before doing anything. What
is automated here is restoring into a disposable target — a drill, a copy for
investigation — not replacing a live database.

### Replacing a live database is a runbook, not a command

It needs, in order: owner approval, maintenance mode, writers stopped, sessions
inspected and terminated, the pool paused. Then rename the current target to a
**rollback name** rather than dropping it, rename staging into place, reconnect,
run smoke checks, and keep the rollback database until the result is accepted.
Dropping it is a separate approved action, later.

The difference that matters: never drop the old database before a rollback name
exists. This script's `DROP` then `RENAME` is acceptable for a disposable target
precisely because there is nothing to roll back to.

## The local daily dump

**An operational copy. Not disaster recovery.** It lands on the same disk as the
database it came from, so it does not protect against losing the host. What it
does is move RPO from *undefined* to *24 hours while the server is alive*, which
is the difference between not knowing what would be lost and knowing.

`scripts/backup_local_daily.sh`, driven by `infra/systemd/sora-backup-local.timer`
at 03:30 UTC with `Persistent=true`, so a run missed while the host was down
executes after boot instead of being skipped.

| | |
|---|---|
| format | `--format=custom --no-owner --no-acl` |
| directory | `/var/backups/sora`, mode `0700`, `umask 077` |
| overlap | `flock`; a second run exits without touching anything |
| verification | `pg_restore --list` before the file is allowed to exist |
| publication | written to `.tmp`, moved atomically only after passing |
| retention | 7 days, pruned **after** a new dump has been verified |
| credentials | none. `pg_dump` runs inside the container; nothing reaches argv |
| failure | journal, and systemd marks the unit failed |

### Two things measured rather than assumed

**Verification happens inside the container.** The first version piped the dump
to the host and checked it with `pg_restore --list /dev/stdin`. That cannot work:
a custom-format archive must be seeked and stdin through `docker exec` is a pipe.
It failed loudly and wrote nothing — the design behaving correctly — but the check
had to move to where the file is a file. Neither `pg_dump` nor `pg_restore` exists
on this host, so there is nowhere else it could go.

**The copy out is size-checked.** The dump leaves the container through a pipe,
so the host-side size is compared against what the container reports. A truncated
transfer would otherwise produce a shorter file that still lists correctly.

### Verified on production, 2026-07-31

```
systemctl start sora-backup-local.service      Finished, unit succeeded
wrote sora_earth_20260731T230702Z.dump         1,174,757 bytes, sha256 recorded
/var/backups/sora                              0700, files 0600
```

Restore drill from that exact automated dump, into a scratch database that was
then dropped:

```
tables                19
region_esg_scores     85 rows
environmental_observations  1,860 rows
```

### What the schedule actually guarantees

**RPO ≤ 24 hours while the timer fires on schedule, for host-survivable loss —
and only that.** Both conditions the review set are met: the unit has run
successfully under systemd, and a restore from what it produced has been
performed.

The qualifier is not hedging. `Persistent=true` runs a missed activation after
boot, which is the right behaviour — but it means downtime extends the gap
between consecutive dumps by however long the host was down. A server off for
six hours yields a 30-hour interval, and no daily schedule can do otherwise.

An earlier version also set `RandomizedDelaySec=300`, which put consecutive runs
up to 24h05m apart on its own, with the host running normally. It bought nothing
here — nothing else is scheduled at 03:30 — and cost the interval the claim rests
on, so it is gone.

### Still not covered

Losing the server, the disk, or the directory. That needs the off-site pipeline
above — encryption to a recipient, S3, manifests, retention — which requires a
keypair and a bucket this host does not have. Separate work, and a decision about
where the bucket lives and who holds the private key.

## RPO: what is claimed and what is not

These are different things and conflating them is how a backup policy becomes
fiction:

| | |
|---|---|
| **Proposed RPO** | ≤ 6 hours, on a four-times-daily schedule |
| **Configured schedule** | none — nothing is installed |
| **Observed interval** | none — no scheduled run has happened |
| **Verified restore** | yes, against PostgreSQL 16 — see below |

### Drill result

Backup and restore of a database carrying the production shape, PostgreSQL 16:

```
fingerprints IDENTICAL — 295 lines
  181 column     63 index      16 table
   16 rows       16 constraint  1 view
    1 content hash              1 alembic revision
```

Refusals, exercised against the objects as actually stored rather than in a unit
test — in every case the database was never touched:

| damage | outcome |
|---|---|
| algorithm line rewritten in the header | refused: payload failed authentication |
| one byte flipped in the ciphertext | refused: checksum disagrees with the manifest |
| manifest claiming a different checksum | refused: checksum disagrees with the manifest |
| none | restored |

The first is what the header authentication is for. Signing only the ciphertext
would have let that edit through to a reader that believed the algorithm line.

### Two drills, and only one of them is about the off-site copy

`scripts/backup_restore_drill.sh` starts from a dump it has just written. It
proves the database can be lost and rebuilt, and it would pass with an empty
bucket: it never downloads, never decrypts, and never reads a manifest.

`scripts/backup_offsite_drill.sh` starts where a real disaster starts — with
nothing but the store:

```bash
BACKUP_IDENTITY_KEY=/path/to/identity.pem \
PG_CONTAINER=sora-drill-pg \
    ./scripts/backup_offsite_drill.sh [backup-id]
```

It selects a **completed** set (one with a manifest — never the newest
directory, which may be an upload that died), checks every part is present
before downloading anything, verifies the ciphertext against the manifest's
size and hash, refuses to decrypt if either disagrees, verifies the decrypted
bytes against `dump_sha256`, reads the table of contents, restores into a
temporary database of its own naming, and compares the result with the
fingerprint stored beside the backup. The temporary database and the plaintext
are removed on every exit path.

`dump_sha256` was added with that drill. Sets written before it carry no
plaintext hash, and the drill prints `SKIP` for that step rather than passing
it — an unverifiable step must not read as a verified one.

The IAM policies for the two identities this needs are in
[BACKUP_S3_IAM.md](BACKUP_S3_IAM.md). Neither the bucket nor the credentials
exist yet; that is an owner decision.

### What the drill proves, and what it does not

Two distinctions are worth keeping apart, because conflating them overstates the
guarantee.

**Verified** — the fingerprint compares these before and after, and they matched:

| | count |
|---|---|
| tables | 16 |
| columns, with type, nullability and default | 181 |
| indexes, with definition | 63 |
| constraints, with definition | 16 |
| views | 1 |
| row counts, per table | 16 |
| content hash of `region_esg_scores` | 1 |
| Alembic revision | 1 |

**Restored but not verified.** `pg_dump -F c` carries these and `pg_restore`
puts them back; the fingerprint simply does not compare them, so a fault in that
path would not be caught here:

- current values of sequences
- extensions
- triggers and functions
- large objects

**Not carried at all:**

- roles — cluster-level, absent from a single-database dump
- database-level settings

**Ownership** is deliberately not restored: `pg_restore --no-owner`. Reproducing
it needs the same roles to exist and can require superuser.

**Grants are a separate question from ownership**, and the answer is not the one
that sounds obvious. Measured on PostgreSQL 16 with the exact commands these
scripts run:

| target cluster | outcome |
|---|---|
| grantee role exists | ACL restored **byte-identically**, named grant and `PUBLIC` alike |
| grantee role absent | the whole `GRANT` group fails; **every ACL on that table is lost**, including the `PUBLIC` grant that did not depend on the missing role |

`--no-owner` suppresses ownership; it says nothing about privileges. Grants are
in the dump and are restored — provided the roles are there to receive them.

### A non-zero exit is not a rollback

`pg_restore` exits 1 when the GRANT fails — but reporting failure is not the
same as undoing anything. It does not roll back the statements it already
applied, so an ordinary restore leaves the target **partially populated**, which
is worse than empty because it looks usable. Measured:

| restore | exit | what is in the database afterwards |
|---|---|---|
| plain `pg_restore` | 1 | the table, with all 50 rows |
| `--single-transaction --exit-on-error` | 1 | nothing — `relation "t" does not exist` |

The first row is the trap: an operator sees a failure, finds a populated
database, and reasonably concludes it mostly worked. What is missing is the
privileges, and nothing says so.

So the restore is transactional, and it does not touch the target until it has
something good to put there:

```
create staging → restore into it → drop the target → rename staging into place
```

An earlier version dropped the target first. That destroys what was there
before the replacement is known to work — the failing restore above would have
left nothing to go back to. Verified: with the role missing, a target holding
its own data came through untouched.

This is a **database** backup, not a cluster backup.

**The actual RPO today is undefined.** Not poor — undefined. Nothing takes a
backup on a schedule, so what would come back is whatever someone last ran by
hand, of unknown age. Restoring in seconds does not help with that.

Choosing the schedule is a decision about acceptable data loss, and installing
it is an owner action. Nothing here touches production.

## What the restore evidence means

Two numbers appear in the drill output and they measure different things:

| | |
|---|---|
| **85** | rows in `region_esg_scores` — the seeded production shape |
| **295** | lines in the fingerprint, one per recorded fact |

The fingerprint is not a row dump. It records the Alembic revision, every table,
every view, every column with its type, nullability and default, every
constraint with its definition, every index with its definition, a row count for
each table, and an MD5 over the ordered contents of `region_esg_scores`.

Comparing those 295 lines before and after a restore is what makes "identical"
mean something: a dump that restored the rows but lost an index, a constraint or
the revision would differ, and a row count alone would not notice.

## Portability

Written for bash 3.2, which is what macOS ships: no `mapfile`, no associative
arrays, no `flock`. Locking uses `mkdir`, which is atomic on every POSIX
filesystem.

`flock` is worth naming specifically. It is absent on macOS, and its absence
produced the dangerous failure rather than a loud one: the script reported that
another run held the lock, so a schedule would have looked healthy while backing
up nothing at all.
