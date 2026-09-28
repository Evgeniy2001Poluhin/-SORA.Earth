# Off-site backup: least-privilege policies

**Examples with placeholders. Nothing here is a working identity, and applying
it creates nothing.** Replace `BUCKET-NAME` and `PREFIX` with real values; the
bucket itself is an owner decision that has not been taken
(`docs/API_CONTRACT_ROADMAP.md`).

## Three roles, because they fail differently

The backup pipeline does three things with the store, and giving one identity
all three means a compromised backup host can erase the backups it just wrote —
which is the failure the off-site copy exists to survive.

| role | used by | may | may not |
|---|---|---|---|
| **writer** | `scripts/backup_run.sh` on the production host | `PutObject`, `AbortMultipartUpload` | read, list, delete |
| **restorer** | `scripts/backup_restore.sh`, `scripts/backup_offsite_drill.sh` | `GetObject`, `ListBucket` under the prefix | write, delete |
| **janitor** | `scripts/backup_retention.sh`, only if provider lifecycle rules are not enough | `DeleteObject`, `ListBucket` | write |

No `s3:*` anywhere, and no policy without a `Resource` naming the bucket and
the prefix.

### The writer cannot read what it wrote

That is deliberate and already relied on:
`tests/test_backup_automation.py::test_the_backup_host_cannot_read_its_own_backups`
asserts the *encryption* side of the same property — the host encrypts to a
public key it cannot decrypt with. The policy is the other half. A writer that
can read is a host whose compromise discloses every historical backup;
a writer that can delete is one whose compromise removes the recovery path.

`backup_run.sh` verifies its own upload with `store_size` (a `HeadObject`) and
`store_get` of the checksum file. **Those need read access**, so a pure
`PutObject` writer fails at the verification step. Two honest options, and the
choice is the owner's:

- give the writer `GetObject`/`HeadObject` **restricted to `*.sha256`**, which
  is what the verification actually reads (shown below), or
- drop the post-upload verification and rely on the drill instead — cheaper
  policy, later detection.

The first is written out here because a backup that is not verified at write
time is discovered wrong at restore time.

## writer

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "WriteBackupObjects",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:AbortMultipartUpload"],
      "Resource": "arn:aws:s3:::BUCKET-NAME/PREFIX/*"
    },
    {
      "Sid": "VerifyOwnUploadOnly",
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::BUCKET-NAME/PREFIX/*.sha256"
    }
  ]
}
```

## restorer

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadBackupObjects",
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::BUCKET-NAME/PREFIX/*"
    },
    {
      "Sid": "ListOnlyTheBackupPrefix",
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::BUCKET-NAME",
      "Condition": {"StringLike": {"s3:prefix": ["PREFIX/*"]}}
    }
  ]
}
```

`ListBucket` is on the **bucket**, not on the objects — a common mistake that
produces `AccessDenied` on a listing while single-object reads work, which
reads as "the backup is missing".

## janitor

Only if the provider's lifecycle rules cannot express the retention policy.
`scripts/backup_retention.sh` keeps a rolling window plus weekly picks, which
most lifecycle configurations cannot describe; if yours can, prefer it and do
not create this identity at all.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DeleteExpiredBackups",
      "Effect": "Allow",
      "Action": ["s3:DeleteObject"],
      "Resource": "arn:aws:s3:::BUCKET-NAME/PREFIX/*"
    },
    {
      "Sid": "ListOnlyTheBackupPrefix",
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::BUCKET-NAME",
      "Condition": {"StringLike": {"s3:prefix": ["PREFIX/*"]}}
    }
  ]
}
```

## Provider-neutral, which is the point

`scripts/backup_store.sh` speaks to any S3-compatible endpoint through
`--endpoint-url`, and `tests/test_backup_automation.py::test_the_endpoint_url_reaches_the_client`
asserts the argument survives. On a provider that is not AWS the policy
document differs in form; the division into three roles does not, and it is the
division that carries the safety.

Some providers offer no per-prefix conditions. Then use **a bucket per role's
scope** rather than widening the policy — a writer that can read the whole
bucket is the thing this document exists to avoid.

## Credentials

Never in `docker-compose.yml`, never in a shell profile, never in argv.
`backup_store.sh` reads them from files named by
`BACKUP_S3_ACCESS_KEY_FILE` and `BACKUP_S3_SECRET_KEY_FILE`, and exports them
only into the client's own environment — asserted by
`test_no_script_puts_a_credential_in_argv`.

On this host the files live in `/etc/sora-earth`, `0400`, owned by `sora`,
and `/etc/sora-earth/backup.env` names them:

```sh
BACKUP_S3_ACCESS_KEY_FILE=/etc/sora-earth/s3-access-key
BACKUP_S3_SECRET_KEY_FILE=/etc/sora-earth/s3-secret-key
```

`sora-backup.service` runs as `sora` and reads `backup.env` through
`EnvironmentFile=`, so the file holds paths and settings, never a key. The unit
carries no inline `Environment=` except the runtime directory -- asserted by
`tests/test_backup_schedule.py::test_no_secret_is_written_into_the_unit` -- so
the credentials cannot drift into the world-readable unit file either.

This section used to recommend systemd `LoadCredential=` with the files under
`/etc/sora`, `0400 root:root`. The unit never did that, the test forbids the
inline `Environment=` lines it needs, and a service running as `sora` cannot
read a `root`-only file: following that advice produced keys the backup could
not use. `LoadCredential=` would be a reasonable design; it would have to arrive
as a change to the unit and its test, not as advice beside them.

## What still needs a decision

Creating the bucket, choosing the provider, and issuing the two identities.
None of it is done here, and no credential in this repository is real.
