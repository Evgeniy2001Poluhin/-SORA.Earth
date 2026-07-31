# SORA.Earth Maximum — Roadmap

**Updated:** 2026-07-31 · **main:** `531822b` · supersedes the M0-only plan in
`M0_EXECUTION_PLAN.md`, which stays as the historical record of that milestone.

Every claim below is either marked as verified with the evidence that verified it,
or marked as unverified. Nothing here is asserted from memory. Where a fact could
only be established against production, it is listed as unverifiable **by this
agent** — production access is withheld after a registered incident on 2026-07-27,
and documentation is not permission.

---

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
| Observations actually persisting in production | **Unverified.** The 2026-07-24 audit recorded `environmental_observations` = 0 rows. Persistence code and tests have landed since (`test_environmental_persist.py`, `test_persist_commit_failure.py`, `test_openmeteo_ingester.py`) and pass in CI, but CI is not production |
| Backup on a schedule | Scripts exist (`scripts/backup_*.sh`). No schedule is installed. RPO is undefined — not poor, undefined |
| Restore drill against production | Done locally against PostgreSQL 16. Never against production |
| Security P0 | Three private advisories with matching fix branches. PRs #46, #47, #48 open; #45 draft |
| Issue #51 remainder | Reverse direction of the schema check with an allowlist; stray tolerated objects; a cold-start race that did **not** reproduce in five attempts and is recorded as unreproduced rather than dropped |

### Verified stale, closed

`#3` (export/csv 404) and `#22` (region_esg_scores migration) no longer reproduce.
`#36` was closed in code by PR #44 and had merely stayed open in the tracker —
found by checking, not by reading the list.

---

## M1 — Data Trust

**Prerequisite:** M0 complete *and* observations persisting. The first is done. The
second is the one open question, and it is a single query against production:

```sql
SELECT count(*), max(observed_at) FROM environmental_observations;
SELECT job_name, status, count(*), max(finished_at)
  FROM environmental_job_log GROUP BY 1, 2;
```

If rows are accumulating, M1 opens. If not, the first item of M1 is repairing
ingestion, and nothing downstream is worth building first.

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

### A gap found while writing this

The spec requires four distinct times per observation. `EnvironmentalObservation`
carries three:

```
observed_at    when the phenomenon occurred          present
created_at     when this system stored the row       present
updated_at     when the row last changed             present
published_at   when the source made it available     MISSING
```

Without `published_at`, a late value cannot be attributed: "the source published
it late" and "we fetched it late" are indistinguishable, and freshness SLAs
against a source become unmeasurable. This is a schema change and therefore an
early M1 item, before contracts are written against a shape that cannot express
them.

### Work

| | |
|---|---|
| **Source register** | For each source: endpoint, auth, licence, owner, update frequency, geographic and temporal coverage, known gaps. Sources whose endpoint is not in the code get their provenance established first |
| **Data contracts** | Schema, units, valid ranges, required fields, geographic precision, temporal frequency, freshness SLA, missing-value handling, deduplication rule |
| **Four timestamps** | Add `published_at`; migration plus backfill policy for rows that predate it |
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
| **M2 Forecasting Lab** | M1 complete. Baselines first (naive, seasonal naive, moving average); rolling-origin backtesting, never a random split; ≥ 15% improvement over seasonal naive or the model does not ship |
| **M3 Crisis Intelligence** | M2 complete, event-labelled benchmark with confirmed dates. Recall, precision, false-alarm rate and lead time on real events — not thresholds asserted to work |
| **M4 Safe MLOps** | M3 complete. Champion/challenger, shadow, rollback. A retrain succeeding is not grounds for promotion |
| **M5 Specialist Workspace** | M4 complete. UAT with 5–10 specialists on real tasks |
| **M6 SORA Copilot** | M5 complete. Numbers come from tools, never from the model. Citation precision ≥ 95% |
| **M7 Validation Pack** | M6 complete. Model cards, dataset cards, security and DR evidence tied to a release tag |

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

---

## What this agent will not do

Production SSH, production database access, production migrations, deployment,
force-push, rebasing published branches, changing branch protection, rotating
secrets, or declaring a milestone complete. These are owner actions. The one
open M1 prerequisite falls in that set, which is why it is stated as a question
rather than an answer.
