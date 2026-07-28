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

The scheme is hybrid, which is what `age` and GPG do internally:

| object | contents |
|---|---|
| `payload.enc` | AES-256-CBC under a per-backup data key |
| `payload.mac` | HMAC-SHA256 over the ciphertext |
| `payload.key` | the data key, sealed to an RSA public key with OAEP |

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
| **Verified restore** | yes, against PostgreSQL 16: fingerprint identical across 295 lines, 85 rows and the view intact |

**The actual RPO today is undefined.** Not poor — undefined. Nothing takes a
backup on a schedule, so what would come back is whatever someone last ran by
hand, of unknown age. Restoring in seconds does not help with that.

Choosing the schedule is a decision about acceptable data loss, and installing
it is an owner action. Nothing here touches production.

## Portability

Written for bash 3.2, which is what macOS ships: no `mapfile`, no associative
arrays, no `flock`. Locking uses `mkdir`, which is atomic on every POSIX
filesystem.

`flock` is worth naming specifically. It is absent on macOS, and its absence
produced the dangerous failure rather than a loud one: the script reported that
another run held the lock, so a schedule would have looked healthy while backing
up nothing at all.
