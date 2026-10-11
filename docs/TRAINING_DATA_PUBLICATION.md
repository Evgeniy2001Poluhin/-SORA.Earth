# Training dataset publication

PR #446 selects the CSV and its immutable manifest through one filesystem
pointer. This document describes the code contract, not an executed production
migration or authorization to deploy the stack.

## Writers and readers

Inventory: `git grep -n 'projects.csv' -- app scripts`.

| Path | Behaviour |
| --- | --- |
| `POST /model/data/refresh` | Locks before reading, publishes snapshots, switches pointer |
| Both bulk upload routes | Same operation through `_ingest_frame` |
| `scripts/enrich_worldbank_dataset.py` | Separate export only; active CSV, sidecar and snapshot store refused before network or writes, including raw/temp outputs |
| `scripts/fetch_wb_projects.py` | Separate export only; same refusal for output, temp and manifest |
| Other references in `app/` and `scripts/` | Read inputs or produce other artifacts; do not replace the active training CSV |

The CLI guards cover resolved symlinks and existing hardlink aliases. They are
accidental-overwrite prevention, not a sandbox against a malicious local user
who can replace filesystem entries after a check.

## Commit protocol

1. Acquire the existing interprocess dataset lock before reading current rows.
2. Verify an existing snapshot pointer or legacy sidecar. Missing provenance in
   a plain legacy CSV remains explicitly unknown; preserve its original bytes.
3. Publish an immutable operator submission and a composite snapshot. The
   composite records parent and submission IDs and retains parent source and
   contract versions. Unknown legacy bytes live in `raw.bin` of the first
   composite. A source is never inferred from a CSV filename.
4. Create a relative symlink beside `projects.csv` pointing to the composite's
   `normalized.bin`, then atomically replace `projects.csv` with that link.
   The manifest lives beside the immutable target. It is never separately
   switched. Any old sidecar remains untouched for audit and is ignored once
   the active CSV is a snapshot pointer.
5. Sync the containing directory before reporting success. Retraining starts
   only after that success. Readers resolve the pointer once and verify bytes
   against that version's manifest, even if a later publisher switches it.

Before the rename, failures leave the active version unchanged. Unreferenced
complete snapshots may remain and are not automatically deleted. A process
crash may leave an unused temporary pointer, also not selected by readers.
After rename, a directory-sync I/O error returns HTTP 503 with **durability
unconfirmed** and does not start retraining. The new complete version is
visible; callers must inspect before retrying to avoid duplicate submissions.
There is no claim that the dataset is unchanged in this case. A machine crash
on a filesystem without directory fsync support has weaker durability; only
`EINVAL`/`ENOTSUP` are treated as lack of support, not arbitrary I/O failures.

The CSV remains readable by pandas and ordinary file readers. Writing through
the symlink is forbidden: it would corrupt an immutable snapshot. The store is
application-immutable, not an OS-enforced write-once store. Local administrators
with write access remain inside the trust boundary.

## Runtime identity and rollout gate

`scripts/deploy_production.sh` exports the checked-out Git SHA before building;
Compose forwards it as a build argument and the runtime image retains it as
`SORA_GIT_SHA`. A rollback source rebuild uses the rollback revision. Production
publication refuses absent, malformed or abbreviated SHA before publishing;
it never invokes Git inside the image. Offline development may resolve Git.

Before deploying #441/#446, explicitly provision `SORA_DATA_SNAPSHOT_ROOT` and
durable storage for the active dataset, its lock, and the referenced snapshot
store. Backend and scheduler must see the same paths and versions. The existing
Compose configuration does **not** yet configure this dataset storage: its
shared `/app/runtime` volume currently serves models, while `/app/data` is an
image copy. Adding SHA alone does not close this deployment gate.

Also publish/verify the initial dataset before scheduled retraining: #441
intentionally refuses a CSV without snapshot evidence. Do not invent lineage
for historical rows or silently label an unverified seed as World Bank data.
Provisioning/initial publication and rollback compatibility require a reviewed
rollout plan. Old application versions can write through symlinks and therefore
must not be restarted against the new active pointer without adapting storage.
No production access or storage mutation is performed by this PR.
