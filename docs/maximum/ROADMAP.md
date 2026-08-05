# SORA.Earth Maximum — Roadmap

**Updated:** 2026-08-05 · **main:** `a3f7f4c` · **production:** `5987547` ·
supersedes the M0-only plan in `M0_EXECUTION_PLAN.md`, which stays as the
historical record of that milestone.

Production is several merges behind main. That gap is stated wherever it changes
what is true, because "merged" and "running" are different claims and this
document has confused them before.

Every claim below is either marked as verified with the evidence that verified it,
or marked as unverified. Nothing here is asserted from memory. Where a fact could
only be established against production, it says so and names how it was checked.
Production access is withheld by default after a registered incident on
2026-07-27; the read-only checks recorded below were made after the owner gave
permission directly in chat. Documentation is still not permission.

---

## Landed since this document was last written (2026-07-31 → 2026-08-04)

| | |
|---|---|
| #63, #66, #67 | The daily local backup is covered by behavioural tests and mutation testing, and a deployment guard refuses a non-main, dirty or out-of-sync tree. The guard's rollback path has never been exercised on production |
| #64, #65 | Public traffic was being served by a 16-day-old dev container with `SORA_OFFLINE=1`. nginx now routes to the production backend, and the dev compose file no longer publishes the API to the internet |
| #68 | A run is a success only when it produced something. Production shows it working: `openaq_ingestion \| degraded \| processed=0` where the same run used to record `success` |
| #45–#48 | The four security fixes, merged after their branches were brought up to date. All four had shown `CLEAN` on CI that ran five days earlier against a base 55–58 commits stale; updating each turned it `UNSTABLE` or `BLOCKED` immediately |
| #72 | The aggregation recorded nothing about what it aggregated |
| #73 | **Merged.** Records, per row, why an indicator has no period — and whether it can be inferred, which is a weaker claim than the source stating it. Capability only: #58 stays open, and the 90,461 production rows are untouched until a separate controlled operation |
| #74, #75, #76 | Filed 2026-08-04 from external review: operator-actionable ingestion status, point-in-time provenance, and an owned lifecycle for long waits |

## Where the project actually is

### Closed, with evidence

| | evidence |
|---|---|
| `alembic upgrade head` builds the whole schema on a clean database | PR #52, merged `63997f7`. Four tables that existed only via `create_all()` now have migrations. Verified on PostgreSQL 14, 15, 16, 17 and under `search_path = 'app_schema, public'` |
| A revision refuses a schema it cannot vouch for | Structured catalogue comparison, never rendered DDL. Extras classified: index / nullable / defaulted / generated column tolerated; `NOT NULL` without default, unique index, any constraint, trigger, rule, row-level security refused |
| Alembic is the sole owner of the schema | PR #53, merged `531822b`. `init_db()` gone from the import path, `app.database.init_db()` deleted. `assert_schema_ready()` checks tables, columns, nullability both ways, and type compatibility on PostgreSQL |
| The application refuses to start on a schema that does not match | Asserted through the real FastAPI lifecycle, not by calling the function: `with TestClient(app)` starts on a migrated database and is refused on one with a column dropped |
| Scheduler metrics are recorded at all | PR #55. Six counters were imported from a module that defines none of them; the `ImportError` was caught and all six set to `None`. Nothing was counted and nothing failed |
| Importing the scheduler no longer loads the ML stack | Same fix. Subprocess check: `app.main` is not in `sys.modules` after `import app.scheduler` |
| CI stopped masking | `environmental-postgres-tests` went from cancelled at its limit to 3m47s. `integration-tests` had **no schema step at all** and built its tables by importing the application — invisible while `init_db()` existed |

### Open, and not closed by the above

| | state |
|---|---|
| Observations actually persisting in production | **Now yes.** 24,230 rows, verified 2026-08-04. This was 0 on 2026-07-31 and was the one open M1 prerequisite |
| Production running current migrations | **Yes for the code it runs.** `alembic_version` = `c4d1f8a26b93`, which is head on `main`. The *code* is `5987547`, so #45–#48 and #72 are merged and not deployed |
| Disaster-recovery backup on a schedule | **No.** `sora-backup.timer` shipped in #47 and is not installed — production predates that merge. The unit is not on the host at all |
| Operational daily dump | **Yes.** `sora-backup-local.timer` enabled and active; last run 2026-08-04 03:30:07 UTC, exit 0. Three dumps on disk with checksums, 1.4M → 1.5M → 1.7M. Not disaster recovery: same host |
| Restore drill against production | Done locally against PostgreSQL 16. Never against production |
| Security P0 | **Merged**, not deployed. #45, #46, #47, #48 are all in `main`; production runs code from before them |
| Deployment is atomic | **No.** #71. Worse than first recorded: eight checks run *after* `up`, and `fail` only exits — so a forbidden port is named accurately and left open, and the deployment stops in a state worse than the one it began in. PRs #79 (refuse before `up`) and #80 (roll back after it) are open |
| Issue #51 remainder | Reverse direction of the schema check with an allowlist; stray tolerated objects; a cold-start race that did **not** reproduce in five attempts and is recorded as unreproduced rather than dropped |

### Verified stale, closed

`#3` (export/csv 404) and `#22` (region_esg_scores migration) no longer reproduce.
`#36` was closed in code by PR #44 and had merely stayed open in the tracker —
found by checking, not by reading the list.

---

## M1 — Data Trust

**Prerequisite:** M0 complete *and* observations persisting. **Both are now met**,
as of 2026-08-04:

```sql
SELECT count(*) FROM environmental_observations;   -- 24,230   (was 0 on 07-31)
SELECT version_num FROM alembic_version;           -- c4d1f8a26b93  (was 0b0ff6d1594e)
```

The column is `event_time`, not `observed_at` — the first version of this query
used the wrong name and failed, which is how the schema below came to be read
properly rather than assumed.

Persisting is not the same as trustworthy, which is what M1 is for. Two things
are already known to be wrong with what is being written:

* **90,461 of 93,623 `country_indicator_history` rows carry no observation
  period** (#58). #59 stopped the loss for new rows; #73 is merged and records,
  per row, whether the missing period can be inferred and under which rule. The
  proportion has not moved: merging the capability changes no data, and the
  backfill has deliberately not been run against production.
* **A run recorded `success` while producing nothing**, 333 times in a row
  (#56). #68 made a run a success only when it produced something, and
  production confirms it. #74 is the rest of that: a status an operator can act
  on rather than read.

### Sources, as they exist in the code today

Established by inventory, not from the roadmap prose:

| ingester | source |
|---|---|
| `app/ingesters/openaq.py` | OpenAQ v3 — air quality, requires `OPENAQ_API_KEY` |
| `app/ingesters/openmeteo.py` | Open-Meteo — weather, no key |
| `app/ingesters/rosstat.py` | Rosstat — no URL constant in the module |
| `app/ingesters/sber_veb_baseline.py` | Sber/VEB baseline — no URL constant in the module |

Both of the last two need their provenance established before a contract can be
written for them: a source with no endpoint in the code is either a file drop, a
manual load, or dead. That is the first task, not an assumption to carry forward.

### Verified against production, 2026-08-04

Read-only, with the owner's explicit permission given in chat.

| | |
|---|---|
| deployed code | `5987547` — four merges behind `main` |
| `alembic_version` | `c4d1f8a26b93` |
| `environmental_observations` | 24,230 rows |
| `country_indicator_history` | 93,623 rows, 90,461 with no `as_of_date` |
| `sora-backup-local.timer` | enabled, active; last run 2026-08-04 03:30:07 UTC, exit 0 |
| `sora-backup.timer` | not installed on the host |

The queries, so the figures above can be re-derived rather than taken on trust.
Unqualified: this database keeps these tables in the default search path, and
naming a schema that does not exist here would make the check fail in a way that
looks like missing data.

```sql
SELECT count(*) FROM environmental_observations;                  -- 24230
SELECT version_num FROM alembic_version;                          -- c4d1f8a26b93
SELECT count(*) FILTER (WHERE as_of_date IS NULL), count(*)
  FROM country_indicator_history;                                 -- 90461 | 93623
```

```bash
systemctl list-timers --all | grep -i sora   # sora-backup-local.timer, next 08-05 03:30
ls -lh /var/backups/sora/                    # three dumps, each with a .sha256
```

**A wrong reading, corrected before it was written down.** The first query asked
for `sora-backup.timer`, got "inactive" and "0 timers listed", and would have
supported the claim that nothing backs this system up. The unit that runs is
`sora-backup-local.timer`; asking for the wrong name produced an answer that
looked like evidence of absence. The dumps on disk — 2026-08-02, -03 and -04,
each with a checksum, growing 1.4M → 1.5M → 1.7M — are what settles it.

**Superseded from 2026-07-31.** That check found 0 observations and
`alembic_version` = `0b0ff6d1594e`, and concluded the M1 prerequisite was not
met. Both facts changed when the deployment on 2026-08-03 landed. Kept here
because the conclusion drawn from them shaped the plan.

**A claim in the first draft of this document was wrong.** It stated that
`published_at` was missing and that only three of the four required timestamps
existed. Production carries all four:

```
event_time     when the phenomenon occurred
published_at   when the source made it available
ingested_at    when this system received it
+ updated_at   when the row last changed
```

The error came from reading `observed_at` at `app/database.py:204` and
attributing it to `EnvironmentalObservation` — it belongs to a different model.
The gap was invented by misreading, not found by measuring. Corrected here rather
than quietly deleted, because a roadmap that hides its own errors is worth less
than one that carries them.

### Work

| | |
|---|---|
| **Source register** | For each source: endpoint, auth, licence, owner, update frequency, geographic and temporal coverage, known gaps. Sources whose endpoint is not in the code get their provenance established first |
| **Data contracts** | Schema, units, valid ranges, required fields, geographic precision, temporal frequency, freshness SLA, missing-value handling, deduplication rule |
| **Four timestamps** | Already present: `event_time`, `published_at`, `ingested_at`, `updated_at`. Nothing to add; the contracts must use them rather than collapse them |
| **Provenance chain** | Source, endpoint, request parameters, checksum of the raw payload, parser version, transformations applied, quality checks run, dataset snapshot id |
| **Quality engine** | Completeness, validity, timeliness, uniqueness, coverage, outliers, unit consistency, source disagreement, schema drift — reported per dimension, never collapsed into one opaque score |
| **Dataset snapshots** | An immutable snapshot per model run, so re-running one reproduces the result |
| **Expert annotation** | Confirm or reject an anomaly, with reason and attachment; machine and expert corrections stay distinguishable |

### Exit gate

- ≥ 99% of records valid against their contract
- 100% of derived values carry provenance
- Re-processing one snapshot reproduces the result byte for byte
- A specialist can open the source of any value shown
- Freshness and coverage visible per source

---

## After M1

Kept deliberately thin. Detail belongs in each milestone's own plan, written when
its prerequisite is met — writing it earlier is how a roadmap becomes fiction.

| | prerequisite |
|---|---|
| **M2 Forecasting Lab** | M1 complete **and #75**. Baselines first (naive, seasonal naive, moving average); rolling-origin backtesting, never a random split; ≥ 15% improvement over seasonal naive or the model does not ship |
| **M3 Crisis Intelligence** | M2 complete, event-labelled benchmark with confirmed dates. Recall, precision, false-alarm rate and lead time on real events — not thresholds asserted to work |
| **M4 Safe MLOps** | M3 complete. Champion/challenger, shadow, rollback. A retrain succeeding is not grounds for promotion |
| **M5 Specialist Workspace** | M4 complete. UAT with 5–10 specialists on real tasks |
| **M6 SORA Copilot** | M5 complete. Numbers come from tools, never from the model. Citation precision ≥ 95% |
| **M7 Validation Pack** | M6 complete. Model cards, dataset cards, security and DR evidence tied to a release tag |

**A correction this document was carrying.** It has been said here and in chat
that closing #58 unblocks leakage-free backtesting. It does not. #58 and #73
establish *when a figure describes*; leakage is governed by *when it became
knowable*, and `published_at` and `superseded_at` do not exist for indicator
history — the row is overwritten on refresh, so "what did we know on date D"
has no answer. #75 is that work, and M2's prerequisite now names it.

---

## Standing rules

Earned during M0, each after being got wrong at least once:

1. **Enumerate the space, not the examples.** A guard tested against
   self-selected cases looks rigorous, which is what makes it dangerous. An IP
   filter missed CGNAT; a type comparison dropped VARCHAR lengths; an "extras are
   harmless" rule did not inspect triggers, rules or row-level security.
2. **Every part of a composite check needs its own negative control.** The index
   comparison verified nothing for several commits while the suite stayed green,
   because an unverified canonical index and a tolerated extra were
   indistinguishable from outside.
3. **Never state an invariant in a comment without a test that fails when it
   stops holding.** That comment is what hid the point above.
4. **Guard the action, not the name.** An allowlist of safe database names cannot
   be completed; "would this actually create anything?" can be answered exactly.
5. **Read the real configuration before pushing.** A rule keyed on database names
   was written while editing the file that names them, and never checked against
   it. Two CI rounds, findable by one `grep`.
6. **A silent `except` is a defect, not defensive coding.** Two of this
   milestone's worst findings were exceptions that turned a break into silence.
7. **Do not raise a timeout to make a failure go away.** Twice it hid the cause.
   Raise the budget only when the work legitimately grew, and say which.
8. **A test must execute the thing it is named after.** The first write-path test
   for #73 spoke to a real PostgreSQL and never invoked the script: it re-typed
   the corrected SQL and checked the database obeyed. That passes forever,
   including on the day the script regresses, because the correct version lives
   in the test. It also ran `alembic upgrade head >/dev/null 2>&1` in a job with
   no Python installed, so ten assertions failed for a reason unrelated to
   anything they tested.
9. **Prove a test can fail.** Every guard added since #63 is checked by
   reintroducing the defect and watching the suite go red. Three mutations were
   run against #73's integration test; each fails exactly one case.
10. **The published state is the claim.** Local edits are not a fix, and a pull
    request whose body describes an abandoned design wastes the whole review.
    Push, then state the SHA.
11. **A refusal is not evidence of absence.** A rate limiter refusing 56 of 118
    requests produced a "40.5% recoverable" measurement that described the rate
    limiter. Paced, it was 76.8%. The same shape appeared again on 2026-08-04:
    querying the wrong systemd unit name returned "0 timers listed", which is
    not the same fact as "nothing is scheduled".

---

## What this agent will not do

Force-push, rebasing published branches, changing branch protection, rotating
secrets, or declaring a milestone complete. These are owner actions.

**Production access changed and this section says so rather than quietly
lapsing.** It was withheld by default after the 2026-07-27 incident, in which
`CLAUDE.md`'s deployment section was treated as permission. The owner has since
granted access directly in chat, and the deployment on 2026-08-03 and the
read-only checks above were made under it. The rule that produced the incident
is unchanged: **documentation is never permission.**

**Writes to production data remain a separate decision, per operation.** The #73
backfill would rewrite up to 90,461 rows and has not been run there. Access to a
database is not authorisation to change it.
