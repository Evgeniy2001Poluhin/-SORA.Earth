# Dataset audit — 2026-09-03

Read-only investigation. No training, no dataset regeneration, no production
change. Answers one question: what is the real, measured provenance of the
training data, and where did "42K" and "700" come from.

**Follow-up:** `docs/DATASET_AUDIT_2026-09-04_LIVE_FETCH.md` runs the live
fetch this document recommended below, measures the actual counts, and
surfaces a target-leakage finding in `duration_months` that bears on the
0.905 AUC cited throughout this document — read it before treating that
figure as trustworthy on its own.

## What is measured, in `data/projects.csv` on `main` (`b697926`)

```
$ wc -l data/projects.csv
17072      (17071 data rows + 1 header)

$ head -1 data/projects.csv
budget,co2_reduction,social_impact,duration_months,category,region,
country_gdp_per_capita,success,name,source
```

Row count by `source`:

| source | rows |
|---|---|
| worldbank | 16,203 |
| synthetic | 851 |
| *(blank)* | 17 |
| **total** | **17,071** |

The model actually serving in production was trained on this file: its
`meta.json` records `total_samples: 17070` (`train_samples: 13656`,
`test_samples: 3414`) and `roc_auc: 0.905`. The one-row discrepancy (17071 in
the file vs 17070 in training) was not chased further here — plausibly a
header-adjacent off-by-one in whichever script split the file — and is worth
five minutes in a follow-up, not blocking.

## There is no project ID in the training schema

`data/projects.csv`'s ten columns carry no identifier — `name` is free text.
**Deduplication or cross-file matching by a stable ID is not possible on this
file as it exists.** This is stated as a finding, not worked around: any
merge of this file against another dataset can only be done by (budget, name,
country, ...) fuzzy matching, which the audit brief itself ruled out as
unreliable.

## Where "734" came from

```
web/src/api/endpoints/driftBaseline.ts:9:
    const STATUS = {exists:true, n_samples:734, feature_count:7,
                    fitted_at:"2026-05-07..."}
```

A hardcoded mock fixture, frozen at a date in May, fixed by PR #216
(`fix/spa-must-not-ship-mock-data`, merged). Not a measurement of anything.
Confirmed by reading the source, not inferred.

## Where "22,000" and "22,851" came from — and why they are not measurements

Two scripts touch World Bank data, with **different output schemas**:

**`scripts/fetch_wb_projects.py`** — the one `data/projects.csv` was actually
built from (its header matches). Requests `id` from the World Bank API
(`FL=... id ...`) and reads it into a local variable `pid`, but `pid` is used
only as a hash seed for a synthetic duration jitter
(`scripts/fetch_wb_projects.py:190`, `synth_duration`) and **is never written
to the output row**. `COLUMNS` (line 374) has no `id` field. Confirmed by
reading the code, not assumed.

**`scripts/enrich_worldbank_dataset.py`** — a separate, never-run script that
*does* keep `project_id` in its output schema (`"project_id": project_id` at
line 313). Its default `--max-projects` is `22000`
(`scripts/enrich_worldbank_dataset.py:65,342`). Its target output,
`data/projects_enriched_wb.csv`, has **no entry anywhere in git history** —
`git log --all --diff-filter=A -- '*projects_enriched*'` returns nothing. It
was never committed to this repository. **No successful full run of
`enrich_worldbank_dataset.py` is confirmed by any saved CSV, manifest, log,
or model lineage.** That is a narrower claim than "it never ran": the script
could have run on the server deleted 2026-08-16 and produced a file that was
never committed and no longer exists anywhere to check. Absence of a
repository artifact proves absence of *provenance*, not absence of the run.

Two documents describe this script's *expected* output as if it were a
result:

- `DATASET_ENRICHMENT.md` line 26: **"Total projects: ~22,000"** under a
  section headed *"After Enrichment (**estimated**)"* — the word "estimated"
  is in the document's own heading.
- `docs/WORLDBANK_DATASET.md` lines 198–213, headed **"Statistics Example"**:
  a formatted console-output block showing `Total projects: 22,851 (851
  original + 22,000 World Bank)`. This is illustrating the format of the
  script's summary printout — the `22,000` in it is a restatement of the
  `--max-projects 22000` default from the command three lines above it, not a
  logged run.

**Neither document, and no file in the repository or its history, records a
World Bank fetch that actually completed and reports how many rows survived
the filters** (`budget<=0` dropped, `duration_months` out of `[1,240]`
dropped, `if not duration_months: return None`, etc.). The `~22,000` figure
that appears in three places is the same input parameter — a request ceiling
— echoed forward, not three independent measurements.

## Where "42,000" and "700" most likely came from

Neither is confirmed. The candidates, ranked by how well they fit what is
measured above:

| figure said | most likely referent | confidence |
|---|---|---|
| "~700" | 734, the mock `n_samples` in the drift-baseline UI (fixed) | high — matches a real number on screen |
| "~700" | 851, the early synthetic-only seed | plausible, less exact |
| "~42,000" | 17,071 (real, in git) + ~22,000 (unverified target parameter, echoed through two docs) | plausible arithmetic, **not a measured sum of unique projects** |

The brief that started this audit was explicit that 17K + 22K cannot be added
as unique training examples without checking for overlap, duplicates, and
rows that failed filters. That check cannot be performed, because the 22K
side of the sum has never been produced as a file.

## The identity schema needed before any re-fetch

Adding `id` to `fetch_wb_projects.py`'s output is necessary but not
sufficient. A single `id` column invites future collisions if a second source
is ever merged in (nothing stops a WorldBank ID and some other source's ID
from colliding as raw strings). The schema needs:

| column | meaning |
|---|---|
| `source` | `worldbank`, `synthetic`, or a future source name |
| `source_project_id` | the ID as the source issued it, e.g. World Bank's `P505244` |
| `source_record_version` | optional — a revision/timestamp if the source can update a record in place |

For World Bank rows the join key used everywhere downstream should be the
composite `worldbank:<source_project_id>`, not the bare source ID, precisely
so it cannot collide with a future source's own numbering. Rows with no
external ID (the `synthetic` rows) need a normalized synthetic ID minted once
and kept stable — not regenerated on every load, or "stable" is not true of
it.

## The 17,071 vs 17,070 discrepancy — traced, not assumed

One row's difference between the file's row count and the training script's
recorded `total_samples` was noted above and deliberately not explained
by guessing. Checked so far: all 17,071 rows have a non-empty `budget` and a
`success` value in `{0, 1}` — so the discrepancy is not explained by either of
those two fields being invalid. That rules out two candidate causes; it does
not identify the real one.

The full trace this needs, to be produced by whatever script actually built
the model that is serving today (not re-derived from scratch by guessing at
its logic):

```
CSV rows
  → rows after schema validation
  → rows after cleaning
  → rows after target filtering
  → unique project identities
  → train
  → validation
  → test
```

With counts recorded at every arrow, and the exclusion reason recorded for
every row dropped at each step. The invariant to check is:

    N_accepted = N_train + N_validation + N_test

(with `N_validation = 0` if no validation split exists.) This has not been
run — it requires reading the actual training script's split logic line by
line, which is separate work from this audit, and is called out here so it is
not silently skipped before the next retrain decision.

## What this means for the plan

The "restore the 22K enrichment" step cannot start from "sort out what
already exists" — nothing exists to sort out. It has to start from **running
`scripts/fetch_wb_projects.py` (or `enrich_worldbank_dataset.py`) against the
live World Bank API in an isolated environment**, for the first time on
record, and measuring — not assuming — what comes back.

Before that run is useful for anything beyond curiosity, `fetch_wb_projects.py`
needs one change: **carry `id` (World Bank's own project ID) into `COLUMNS`**.
Without it, whatever this run produces will have the exact same defect as
`data/projects.csv` has today — no stable key to de-duplicate against a future
re-fetch, or against `data/projects.csv` itself. This is a one-line schema
change and is the natural first step of Phase 1, not a separate task.

## What is NOT claimed here

- Not claimed: that 22,000 WorldBank rows can be fetched today. The API may
  return a different portfolio size than it did on 2026-07-27, when
  `docs/WORLDBANK_DATASET.md` was written (`"total": 45234` in its example is
  itself dated to that day, if it was ever a real response rather than a
  constructed example — this was not called to production to check).
- Not claimed: that `data/projects.csv`'s 16,203 `worldbank` rows and a fresh
  fetch would overlap, be disjoint, or supersede one another. That is exactly
  the question Phase 1 (dataset inventory + manifest) exists to answer, and it
  requires the ID-carrying fetch above as a precondition.
- Not claimed: any number as "the" dataset size going forward. The one number
  that is solid today is **17,071 rows / 17,070 training rows, ROC AUC 0.905,
  currently serving in production** — and that is what today's model is
  actually built on, regardless of what a larger dataset might later look
  like.

## Immediate recommendation

Do not attempt a "42K" retrain. No repository evidence supports a 42K file
as the basis of any past or future training run — not that no such file has
ever existed anywhere. Reconstructing one requires a live fetch, from which
the actual count can be measured rather than assumed. The concrete next step
is:

1. Add `source`, `source_project_id` (and optionally `source_record_version`)
   to `fetch_wb_projects.py`'s `COLUMNS` — see "The identity schema needed
   before any re-fetch" above — not a bare `id` column. If that script is
   kept as the canonical fetcher, deprecate or align
   `enrich_worldbank_dataset.py` so there is one script with one schema, not
   two with different ones.
2. In the same change: reject any new World Bank row with an empty
   `source_project_id`, and assert `(source, source_project_id)` is unique
   within the output. Do this as an automated, offline test against a small
   fixture of API responses — not as a manual check on one run.
3. Run the ID-carrying fetch once, in an isolated environment, against the
   live API. Save the raw API response pages before any filtering, separately
   from the transformed dataset.
4. Report actual counts at each stage — fetched, budget<=0 dropped,
   duration-out-of-range dropped, final, unique IDs, duplicate IDs — the way
   the audit brief specified. Write these into a manifest (source URL and
   query parameters, fetch timestamp, commit SHA, per-stage counts, schema
   hash, content hash) rather than only printing them.
4. Only then compare against `data/projects.csv` by ID, and only then decide
   what "the dataset" is.

No step above touches production, retrains anything, or writes to
`data/projects.csv`.
