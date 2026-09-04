# Ablation harness — the instrument for #232

`scripts/ablation_harness.py` exists to answer one question and refuse to
answer anything else: **how much of the model's apparent skill survives when
the contested features are taken away.**

It does not retrain, does not touch `models/` or `data/`, does not register
or activate anything, and does not read a promotion threshold. It fits small
models in memory, compares them the same way, and writes one JSON report.

Related: [#232](https://github.com/Evgeniy2001Poluhin/-SORA.Earth/issues/232),
`docs/DATASET_AUDIT_2026-09-04_LIVE_FETCH.md`.

## Running it

```bash
# A file that carries source_project_id (the #223 schema) -- grouped split.
python3 scripts/ablation_harness.py \
    --data out/fetch_wb_projects_transformed.csv \
    --manifest out/fetch_wb_projects_transformed.csv.manifest.json \
    --out out/ablation_grouped.json

# data/projects.csv carries no project id at all. Grouped split refuses;
# this mode groups by normalised name and is labelled weaker evidence in
# the report itself.
python3 scripts/ablation_harness.py \
    --data data/projects.csv \
    --out out/ablation_legacy.json \
    --legacy-approximate
```

| flag | default | meaning |
|---|---|---|
| `--data` | required | CSV in the projects.csv schema |
| `--out` | required | JSON report path. Refused if under `models/` or `data/` |
| `--manifest` | none | refuse if the dataset's sha256 disagrees with it |
| `--split` | `grouped` | `grouped` or `temporal` (see below) |
| `--legacy-approximate` | off | name-grouping for the id-less legacy file |
| `--threshold` | `0.5` | where accuracy/F1 cut. Recorded in the report |
| `--bootstrap` | `1000` | resamples for the 95% intervals |
| `--seed` | `0` | split assignment and bootstrap |
| `--min-rows-per-split` | `200` | below this an interval is not worth quoting |
| `--variants` | all | comma-separated subset |
| `--force` | off | overwrite an existing report |

## The variants

Declared in one place (`VARIANTS` in the script), each with the reason it
exists, so the matrix is auditable without reading the fitting code.

| variant | features | what its number means |
|---|---|---|
| `current` | budget, duration_months, co2_reduction, social_impact, gdp, category, region | the baseline every other row is compared against |
| `no_duration` | current minus `duration_months` | the gap from `current` is the quantity #232 asks for |
| `no_closing_derived` | every feature not derived from the closing date | on this schema that set is `{duration_months}`, so the list equals `no_duration` — kept separate because they are different claims |
| `leakage_only` | budget, duration_months | if this is close to `current`, the other columns are not what the model reads |
| `esg_only` | co2_reduction, social_impact, gdp, category, region | the nominally-ESG columns. Expected weak; measuring beats assuming |
| `minimal_baseline` | budget | anything that does not beat this is not earning its complexity |

`no_closing_derived` and `esg_only` are declared `must_exclude_target_derived`,
and a feature marked `target_derived` in the `FEATURES` registry cannot appear
in them — the harness refuses rather than quietly reporting a "clean" number
computed on a dirty feature list.

## Splits

**Grouped** is the only mode that produces standard evidence. Every row
carrying one `source_project_id` goes to one part, decided by a hash of the
id, so the assignment is deterministic from `--seed` and grouped by
construction rather than by a shuffle being trusted. `assert_no_id_overlap()`
then checks the property separately, and the count lands in the report as
`split.id_overlap.overlapping_ids` — **it must be 0**.

**Legacy-approximate** exists because `data/projects.csv` has no id column at
all (`docs/DATASET_AUDIT_2026-09-03.md`). It groups by a normalised `name`.
Two different projects sharing a name are grouped as one; **one project
recorded under two names or two versions is still split across parts, and
this mode cannot prevent that.** The report records
`split.evidence_strength = "weaker: grouped by normalised name, not by
project id"`. It must not be presented as a grouped split, and a
legacy-approximate result **cannot on its own close #232** — it is a
diagnostic of the existing construction, not a clean measurement.

## Confidence intervals: cluster bootstrap

Rows belonging to one project are not independent draws. A row bootstrap
over a clustered test set counts one project's agreement with itself as
evidence and reports an interval narrower than the data supports, so the
harness resamples **whole clusters with replacement** whenever the split had
a grouping unit — `source_project_id` under `grouped`, the normalised name
key under `legacy-approximate`. Every variant records which was used:

```
"bootstrap": {"mode": "cluster", "unit": "source_project_id", "n_clusters": 3389}
```

`mode: "row"` appears only when there is no grouping unit at all, and it says
so rather than implying a cluster interval it did not compute. Measured on a
fixture of 40 identical-within, different-between clusters (320 rows), the
cluster interval is **2.78× wider** than the row interval — close to the
√(320/40) ≈ 2.83 the structure implies.

**Temporal refuses on this schema.** Neither file carries a date column, so
an ordering would have to be invented — row order, or a date reconstructed
from `duration_months`, which is the contested column itself. The function
works when given a real date column and is tested that way; against the
current schema it raises `NoTemporalColumn`.

## What it refuses

Every one of these is a separate exception class so a caller (or a test) can
name the one it means:

| refusal | when |
|---|---|
| `MissingProjectId` | grouped split without `source_project_id`, or a blank id |
| `SplitOverlap` | one id in more than one part |
| `TargetInFeatures` | `success` listed as a feature |
| `FeatureIsTargetCopy` | a feature column is an exact copy of the target |
| `MissingFeature` | a requested feature or variant is absent |
| `SingleClass` | one class left after filtering |
| `SplitTooSmall` | a part below `--min-rows-per-split` |
| `DatasetHashMismatch` | `--manifest` disagrees with the file |
| `ResultExists` | output exists and `--force` was not passed |
| `NoTemporalColumn` | `--split temporal` on a schema with no date |
| `TargetDerivedFeatureInCleanGroup` | a leakage feature inside a group declared clean |
| `ProtectedOutputPath` | `--out` under `models/` or `data/` |

Verified against the real legacy file: `--data data/projects.csv` without
`--legacy-approximate` raises `MissingProjectId`, `--split temporal` raises
`NoTemporalColumn`, and `--out data/ablation.json` raises
`ProtectedOutputPath` — in all three cases no output file is created.

## Report schema (`ablation-report/1`)

```
schema_version   "ablation-report/1"
issue            link to #232
dataset          {path, sha256, rows, columns[]}
split            mode                 grouped | legacy-approximate | temporal
                 evidence_strength    "standard" | "weaker: ..."
                 seed, fractions
                 summary.<part>       {rows, positives, negatives,
                                       positive_rate, unique_project_ids}
                 id_overlap           {checked, overlapping_ids}   # must be 0
columns.<name>   {n_missing, n_zero, n_distinct, is_constant,
                  lineage, target_derived}
settings         {threshold, bootstrap_resamples, min_rows_per_split, estimator}
variants[]       {variant, why, features[], excludes_target_derived,
                  metrics{roc_auc, pr_auc, log_loss, brier, calibration_error,
                          threshold, accuracy_at_threshold, f1_at_threshold},
                  bootstrap{mode: cluster|row, unit, n_clusters},
                  confidence_intervals_95.<metric>{ci_lo, ci_hi,
                                                   n_resamples_used}}
not_claimed[]    what the report is not
```

`n_missing` counts empty/absent cells only. **A zero is a value** — the first
pass of the audit this harness serves got that wrong and reported `success`
as 30.34% missing when it was 0%.

## Example output — synthetic fixture, not the real model

Generated from `tests/test_ablation_harness.py::synthetic()`, a 900-row
fixture where `duration_months` is built from the label on purpose. These
are **not** measurements of the serving model:

```
current              roc_auc=1.0     95% CI=[0.99999, 1.0]
no_duration          roc_auc=0.5373  95% CI=[0.4551, 0.6074]
no_closing_derived   roc_auc=0.5373  95% CI=[0.4551, 0.6074]
leakage_only         roc_auc=1.0     95% CI=[0.99999, 1.0]
esg_only             roc_auc=0.4933  95% CI=[0.4145, 0.6379]
minimal_baseline     roc_auc=0.496   95% CI=[0.4004, 0.5801]
```

The fixture is built so the answer is known in advance: a column derived from
the label scores 1.0, and removing it drops the score to chance. That is the
instrument demonstrating it can see the thing it was built to see — on data
whose answer is known, before it is pointed at data whose answer is not.

## What running it will and will not settle

It will produce, for the first time, a measured gap between "with the
contested features" and "without them", on a split that cannot put one
project on both sides.

It will not, on its own:

- define what `success` should mean — that is a domain decision and it comes
  first;
- reproduce the 0.905 figure, which was produced by a different script, a
  different estimator and a different split;
- tell you the serving model is fine or is not. It measures a dataset's
  capacity to be predicted, not a particular deployed artefact.
