# Dataset audit — live fetch and leakage check, 2026-09-04

Follow-up to `docs/DATASET_AUDIT_2026-09-03.md`. That audit established the
identity schema and confirmed no live World Bank fetch was verifiable in the
repository's history. This one runs that fetch once, in a disposable
environment, and measures what comes back — plus a finding it surfaced along
the way that bears directly on whether the currently-serving model's
reported ROC AUC 0.905 can be trusted at face value.

**No training, no dataset regeneration, no production change.**
`data/projects.csv` was read, never written. The live fetch's raw JSON and
transformed CSV are not committed here — per the audit's own scope, this
repository gets the compact numbers and a reproducible script, not the
multi-megabyte artifacts.

## What was fetched

One run of `scripts/fetch_wb_projects.py`'s `fetch_all()` against the live
World Bank projects API, from commit `b85f8515de5f6e0dcb24d2abf96b497ca5a1a8c6`,
in a disposable directory outside this repository:

| stage | count |
|---|---|
| API's declared total (`total` field, first page) | 22,729 |
| rows actually fetched across all pages | 22,729 |
| rows kept after `fetch_all()`'s inline `budget<=0` filter | 16,188 |
| rows with a non-empty `id` | 16,188 |
| unique `(source, source_project_id)` keys | 16,076 |
| duplicate keys | 112 |
| final deduplicated rows written | 16,076 |

## The 22,729 → 16,188 gap is a filter, not lost pages

Page arithmetic settles this exactly: `fetch_all()` pages by 500
(`os=0,500,...,22500`), which is 45 full pages plus one 229-row final page —
`45*500 + 229 = 22729`, matching the declared total exactly. Every row the
API reported was fetched; nothing was lost to incomplete pagination.

The gap is `fetch_all()`'s own inline filter
(`scripts/fetch_wb_projects.py`, `if budget <= 0: continue`), which runs
*before* a row is kept — so what this run's `raw_wb_projects.json` held was
already budget-filtered, not literally every row the API returned. The
in-repo manifest-writing code in `fetch_wb_projects.py` describes this stage
as rows "kept by `fetch_all()` after the `budget<=0` filter" — worth stating
explicitly here since a manifest that just says "rows returned" reads as
unfiltered raw output.

## Duplicates: all exact, none conflicting

Comparing full raw API rows (every field, not a sample) for all 112
duplicate keys: **112/112 are byte-identical**, 0 conflicting. The API
returned the same project twice within one run — not a data-quality problem,
just redundant pages. Deduplicating loses no information.

## Missingness, corrected

An initial pass's `is_missing()` treated the string `"0"` as missing,
conflating a valid class label with true absence. Corrected to "cell is
empty/absent, nothing else":

| field | naive (wrong) reading | corrected |
|---|---|---|
| `success` | 30.34% missing | **0% missing** — every row has `0` or `1` |
| `co2_reduction` | 100% missing | **0% missing in the transformed CSV** (see below — this field is never blank, but that's not the same as being observed) |

## `co2_reduction` is a constant zero, confirmed at the source

The raw API field `co2_emissions_reduced` — explicitly requested via the
fetch's `FL=` field list — is present in **0 of 16,188** raw rows this run.
Checked independently on `data/projects.csv` (production's actual training
file): **0 of 16,203** `worldbank`-source rows have a nonzero
`co2_reduction`. Same finding, both datasets. This column carries no signal;
every value is `fetch_wb_projects.py`'s own default, not an observation.

## `duration_months` leaks the `success` label

`fetch_wb_projects.py` derives two columns from the same fact — whether a
project has a usable `closingdate`:

- `duration_from_dates()`: a real `(closing - board)` month count when both
  dates are present and `closing > board`; otherwise
  `synth_duration(budget, pid)` — a deterministic function of `budget` alone
  (`6*log10(budget)` ± an md5-hash jitter, clamped `[6,72]`).
- `map_success()`: `1` mostly when a project is Closed with a past closing
  date and a real budget (or disbursement > 50%); `0` for
  Active/Pipeline/Dropped/closed-with-no-usable-date.

`budget` and `duration_months` are two of the model's seven
actually-informative features (CLAUDE.md: "nine columns, seven of which
carry information"). A naive rule — *"if `duration_months` looks like the
synthetic formula, predict `success=0`, else `1`"* — measures how much of
the label these two features alone can fake:

| dataset | mode | naive rule accuracy |
|---|---|---|
| this run's live fetch (16,076 rows, exact `source_project_id`) | exact — `synth_duration()` recomputed and compared per row | **88.75%** |
| `data/projects.csv`, `worldbank` rows (16,203 rows, no project id) | approximate — shape test only (see script) | **85.47%** |

Reproducible: `python3 scripts/check_duration_success_leakage.py --data data/projects.csv`.
Tests: `tests/test_check_duration_success_leakage.py` (arithmetic pinned
against hand-computed fixtures, including a mutation check that a wrong
`synth_duration` recomputation would be caught).

**What this does and doesn't show.** It does not prove 0.905 is wrong — that
needs a leave-`duration_months`/`budget`-out retrain-and-compare, not done
here (no retraining occurred; this is read-only measurement). What is
measured: a rule built from a generation-script artifact unrelated to ESG
quality gets within a few points of what would be a strong classifier, using
two of the model's seven working features. Some meaningful share of the
model's apparent skill could be "detect whether this project has closed
yet" rather than genuine ESG assessment, and that can't be ruled out without
checking. This should be resolved — or at minimum, explicitly acknowledged
alongside the 0.905 figure — before it's used to justify a retrain on a
larger reconstructed dataset built the same way.

## Not claimed here

- Not claimed: the exact share of AUC this accounts for.
- Not claimed: that `social_impact` (also synthesized from `log(budget)` per
  `fetch_wb_projects.py`'s own docstring — not a leakage vector by itself,
  but it doubles budget's influence across two columns), `category`, or
  `region` carry no genuine signal — not measured here.
- Not claimed: a fix. Two directions this motivates, neither attempted: (a)
  a leave-`duration_months`/`budget`-out retrain-and-compare to size the
  effect, or (b) re-deriving `duration_months` only from real dates with a
  distinct missing-indicator rather than a budget-shaped synthetic fallback.
  Both require retraining, out of scope for this audit.
- Not claimed: that a second live fetch would return the same 16,188/16,076
  — the World Bank API's pagination was separately observed to not be
  stable run-to-run (different duplicate IDs across two runs on
  2026-09-03/04). Not re-verified with a second full run here.
