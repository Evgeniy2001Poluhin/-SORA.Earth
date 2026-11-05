# Project ownership: the release record

This change removes `.deploy-blocked`. The marker held project ownership on
`main` until the cutover that makes it safe to run had been delivered and
proved. This file records that proof. It also records what has to be true on
the day of the deployment. It is not permission to deploy: the production
deployment needs its own GO (see "Decision" below).

## What is released

| | commit | what it holds |
|---|---|---|
| base | `4067ac23ac031f62e6090c52a913ddfbdd85888d` | accounts, ownership backend and frontend; carries the marker |
| cutover | `46d632c7be878a0a1bb695b1cdaba3ba2502d2c2` | the deployment stops the old backend and scheduler before migrating, pins one image, refuses when in doubt; the rehearsal script |
| integration | `fb6fe4c7950eb1e7d50cde256d93c50dcc1b40ee` | saved-evaluation PDF and the ownership browser suite on top of the cutover. **The production rehearsal ran on this commit.** |
| preparation | `00538cf7b98911dd50dff12de931ae5e6c828502` | `scripts/rehearse_ownership_cutover.sh` made executable; `e2e/ownership/run-local.sh` no longer pipes `docker ps` into `grep -q` under pipefail |
| this change | the commit that adds this file | removes `.deploy-blocked`, replaces the test that pinned it, adds this record |

Between `fb6fe4c7` and this change there is no application, migration or
deployment-script change. To check that, list the files the diff touches:

```bash
git diff --stat fb6fe4c7950eb1e7d50cde256d93c50dcc1b40ee <this commit>
```

The expected list:

- `.deploy-blocked`, deleted;
- `CLAUDE.md` and `docs/DEPLOY.md`: the paragraph about the marker, in prose
  only;
- `docs/OWNERSHIP_CUTOVER_RELEASE.md`;
- `e2e/ownership/run-local.sh`;
- `scripts/rehearse_ownership_cutover.sh`: the mode only;
- `tests/test_project_ownership.py`.

Any other file in that list voids the rehearsal below, and a new one is
required.

## Evidence

### CI, on the exact head of each pull request

| date (UTC) | head | run | result |
|---|---|---|---|
| 2026-11-04 | `46d632c7` | [41834696587](https://github.com/Evgeniy2001Poluhin/-SORA.Earth/actions/runs/41834696587) | every job green, `integration-tests` and `required-checks` included; artefact `rehearsal-evidence`: passed, 43/43, cleanup confirmed |
| 2026-11-05 | `fb6fe4c7` | [41932037928](https://github.com/Evgeniy2001Poluhin/-SORA.Earth/actions/runs/41932037928) | every job green, including `e2e ownership browser tests` |
| 2026-11-05 | `00538cf7` | [41964378161](https://github.com/Evgeniy2001Poluhin/-SORA.Earth/actions/runs/41964378161) | every job green |

The head of this change gets its own run, and that run must be green before
the change is merged.

### The browser suite

The suite has six scenarios and seven tests. It was run locally against a real
stack: PostgreSQL 16, the production SPA build and Playwright.

- On `fb6fe4c7` it passed three times out of three, 7/7 each time. On
  `b462075f`, the commit before the guard fixes, it also passed three out of
  three.
- Negative control M3 made a specialist read only pilot rows. The suite caught
  it: scenario 3 failed (both tests), and so did scenarios 4 and 6.
- Negative control M5 disabled the token-version check in `app/auth.py`. The
  suite caught it: scenario 5 failed with "Expected 401, Received 201".
- Both mutations were reverted, and the tree was confirmed clean afterwards.

### The production rehearsal, 2026-11-05

The rehearsal ran on `109.120.147.12`, from a separate clean clone at
`fb6fe4c7`. It used a temporary PostgreSQL 16 on an internal network with no
published ports.

- **Input.** The image was built from that commit
  (`sha256:be2bfc8a5399f5d36d9eed02ecd5c52681b33ebd71a2ac7c3d2bce88e0292ff8`),
  and its files matched the release. The dump was
  `/var/backups/sora/sora_earth_20261104T033009Z.dump`, sha256
  `4915bdcf643a0fca4ed8fb3d46852fff14acf6fa15b65e10ecb1952f0402852b`,
  re-verified before use.
- **Run.** It started at 00:59:25Z and held the deploy lock itself
  (`deploy_lock_state = held by this rehearsal`). It ran once; there was no
  repeat.
- **Report.** `/root/rehearsal-20261105-fb6fe4c7.json` reads `passed: true`:
  43 checks, 0 breaches.
- **Measured runs.** There were three, each on a fresh restore from
  `f4b7c2d91e06` to `b7d41f2e9c35`:
  - the migration exited 0 and took at most 1.271 s;
  - lock wait was 0;
  - DDL lock hold was at most 0.055 s;
  - bootstrap and the schema check exited 0;
  - the backend reported a healthy database within 8.7 to 9.4 s;
  - the structure was as expected: FK, CHECK, default, indexes and audit
    tables `2|2|2|5|2`;
  - in `evaluations` and `predictions_log`, every old row was public, had no
    owner, and kept its id and original columns.
- **Negative run.** A session held ACCESS SHARE on `evaluations`. The migrator
  refused (exit 1, after 9.96 s of lock wait). The schema stayed at
  `f4b7c2d91e06`, with an identical fingerprint and identical table checksums.
- **Cleanup.** The report confirms everything was removed, and a separate
  search by the rehearsal label found no containers, volumes or networks.
  Production was compared with a baseline taken before the rehearsal. These
  were identical:
  - container ids;
  - the image behind each container;
  - start times (2026-11-03T11:20:49Z);
  - restart counts (0);
  - `sora_earth_app:current`;
  - `/health` 200;
  - alembic `f4b7c2d91e06`.

  The rehearsal image and clone were then removed, and the report and logs
  were kept.

**What the 300 s figure proves, and what it does not.** The reported stop
window was 132.972 s, against a limit of 300 s. That figure is an upper
estimate, not an elapsed time. 120 s of it is budgets taken at their maximum:
60 s for stop, 30 s for drain, and 30 s for nginx and its checks. About 13 s
was measured: the migration, bootstrap, schema check and backend start.

The production corpus at the dump was **3 rows in `evaluations` and 3 in
`predictions_log`**. The limit is proved for that corpus only. A materially
larger corpus needs a new rehearsal before the figure is relied on.

### The lock directory on the host

The rehearsal found `/run/sora` missing on production, so it was created by
hand (0750 root:root). Because `/run` is a tmpfs, that directory would have
disappeared at the next reboot.

On 2026-11-05 at 08:18Z, `infra/tmpfiles.d/sora.conf` was installed as
`/etc/tmpfiles.d/sora.conf`:

- the installed file has sha256
  `a8f11872e97d2ef4a39ea78fcaf656f73505d188e732afbb4f892fea54edcd67`, the same
  blob as in this tree;
- `systemd-tmpfiles --create` exited 0;
- `systemd-tmpfiles --cat-config` lists `d /run/sora 0750 root root - -`;
- before and after the install, these were identical: `/run/sora`,
  `deploy.lock` (inode, mode, owner, mtime), all four application containers,
  and `/health` 200.

## Deployment day

### Decision

The owner of the project decides, after GPT's acceptance. A GO names three
things: the host (`109.120.147.12`), the exact `main` SHA, and fix-forward as
the recovery strategy. The deployment happens in daytime while the owner is
reachable. A GO for one SHA does not carry over to another.

### GO only if every item holds

1. CI is green on the exact `main` SHA being deployed.
2. That SHA has no `.deploy-blocked` file, and
   `git diff --stat fb6fe4c7950eb1e7d50cde256d93c50dcc1b40ee <sha>` lists only
   the files named above. Merges to `main` made after this change count too.
3. A fresh baseline was taken no more than 60 minutes before the start:
   - container ids, images, start times and restart counts;
   - `sora_earth_app:current`;
   - `/health`;
   - alembic revision;
   - free disk;
   - `MemAvailable`.
4. The newest backup in `/var/backups/sora` is under 26 hours old, and its
   sha256 has been verified.
5. Free space on `/` is at least 15 GB, which covers the image build; the
   rehearsal day had 33 GB. `MemAvailable` is at least 2 GB.
6. There is no `/var/lib/sora/deployments/in-progress` journal, and
   `/run/sora` is 0750 root:root.
7. alembic reads `f4b7c2d91e06` on production. If it reads anything else,
   this record does not describe the migration that will run.

### Stop conditions

Stop and decide again if any of these happens:

- The script refuses with exit 1. Before `mutating`, nothing changed. After
  it, the previous images were restored.
- The script exits 76 (`rollback-refused` or `rollback-impossible`). Backend
  and scheduler may be stopped, and the site may not be serving.
  **Do not start containers by hand.** Read the journal and
  `docker compose -p sora_earth_ai_platform ps -a`, then fix forward.
- `/health` from outside, or `/api/v1/health` with the database healthy, does
  not answer within the script's own checks.

### Recovery is fix-forward

Once the ownership columns exist, both barriers in `scripts/deploy_production.sh`
refuse every commit from before project ownership. They are the accounts
barrier and the ownership barrier, and they refuse both an explicit
`--rollback` and the automatic one.

Even where a rollback is allowed, **returning to an older SHA restores code,
not data**. It does not remove owners, scopes or audit rows written since.
Schema downgrades for these revisions are unsupported.

Recovery is therefore a corrected release:

```bash
# on the workstation: the fix lands on main through a pull request with green CI
# on the host, after a GO for the new SHA:
cd /opt/sora_earth_ai_platform
./scripts/deploy_production.sh
```

The deployment, first or corrective, has succeeded only when all of the
following hold:

- the script exits 0 and writes its manifest;
- `https://sora-earth.online/health` answers 200 from outside;
- `/api/v1/health` reports the database healthy;
- alembic reads `b7d41f2e9c35`;
- backend and scheduler run the image the script pinned (the `.Image` of
  each equals the manifest's image id);
- `promtool query instant ... up` shows both scrape jobs up.

A database restore is a separate emergency operation, described in
`docs/BACKUP_RESTORE.md`. It is not part of this recovery.

### Recorded immediately before the deployment

The fresh baseline, the host, the actual `main` SHA and the GO itself are
recorded at the time of the deployment, in the deployment's own report. They
are not recorded here.
