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

Built on OpenSSL because neither `age` nor GPG is present in this environment,
and a script that needs a package nobody installed is a schedule that never
runs.

Authentication happens **before** decryption: a modified payload is refused, not
decrypted and then judged.

Generating the pair — the private half never reaches the backup host:

```bash
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out identity.pem
openssl pkey -in identity.pem -pubout -out recipient.pem
```

## Configuration

| variable | meaning |
|---|---|
| `BACKUP_RECIPIENT_KEY` | public key; the only key the backup host needs |
| `BACKUP_IDENTITY_KEY` | private key; **restore side only** |
| `BACKUP_S3_ENDPOINT` | any S3-compatible endpoint |
| `BACKUP_S3_BUCKET`, `BACKUP_S3_PREFIX`, `BACKUP_S3_REGION` | object location |
| `BACKUP_S3_ACCESS_KEY_FILE`, `BACKUP_S3_SECRET_KEY_FILE` | credentials, read from files |
| `BACKUP_S3_CLIENT` | client executable, default `aws` |
| `BACKUP_KEEP_ROLLING`, `BACKUP_KEEP_WEEKLY` | retention, default 28 and 8 |
| `BACKUP_ALERT_HOOK` | executable called on failure |

Credentials are read from files and exported to the client, never passed as
arguments — an argument list is readable by every process on the host.

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

The second row has a consequence worth knowing before an incident:
`pg_restore` **exits 1** in that case. The table and its rows arrive intact and
only the ACLs are missing, but `backup_restore.sh` runs under `set -e`, so the
restore reports failure. That is the right default — the restore was not
faithful — but an operator seeing it should know the data is present and the
privileges are not.

Restoring into a cluster that already has the roles avoids this entirely. The
drill target does, which is why the drill does not show it.

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
