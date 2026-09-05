# Ablation result — legacy dataset, 2026-09-05

First measurement from the harness merged in #233/#234, run against
`data/projects.csv` — the file the currently-serving model was trained on.

**This result cannot close [#232](https://github.com/Evgeniy2001Poluhin/-SORA.Earth/issues/232),
and is not offered as doing so.** It is `legacy-approximate` evidence: that
file carries no project id, so rows are grouped by normalised `name`, which
cannot prevent one project recorded under two names or two versions from
landing on both sides of the split. What it does settle is the *size* of the
effect #232 describes, on the data that actually produced the cited figure.

## Reproduction

```bash
python3 scripts/ablation_harness.py \
    --data data/projects.csv \
    --out <path outside data/ and models/> \
    --legacy-approximate --bootstrap 1000 --seed 0
```

| | |
|---|---|
| harness commit | `15b2af6e0e470919abfd50f7de297399ede0a393` |
| dataset | `data/projects.csv`, 17,071 rows |
| dataset sha256 | `0e0faffb3aaebd0bddcb8da11ecc0e4ee044bb7f63376d79745093a08648afcd` |
| split | `legacy-approximate`, seed 0, 60/20/20 |
| `evidence_strength` | `weaker: grouped by normalised name, not by project id` |
| estimator | `RandomForestClassifier(n_estimators=200, random_state=0)` |
| intervals | cluster bootstrap, 1000 resamples, unit `_approx_group_key` |
| runtime | 76 s |

Split, as recorded in the report:

| part | rows | positives | negatives | positive rate | clusters |
|---|---|---|---|---|---|
| train | 9,624 | 6,612 | 3,012 | 0.6870 | 8,152 |
| validation | 4,187 | 2,869 | 1,318 | 0.6852 | 2,787 |
| test | 3,260 | 2,234 | 1,026 | 0.6853 | 2,669 |

`id_overlap.overlapping_ids = 0`. Test carries 3,260 rows across only 2,669
clusters — the rows are not independent, which is why the intervals below are
cluster-bootstrapped rather than row-bootstrapped.

## Results

| variant | ROC AUC | 95% CI (cluster) | PR AUC | Brier | log loss | calib. error |
|---|---|---|---|---|---|---|
| `current` | **0.9165** | [0.9057, 0.9282] | 0.9511 | 0.0994 | 0.3607 | 0.0234 |
| `no_duration` | **0.6605** | [0.6375, 0.6836] | 0.7835 | 0.2202 | 0.8266 | 0.1281 |
| `no_closing_derived` | 0.6605 | [0.6375, 0.6836] | 0.7835 | 0.2202 | 0.8266 | 0.1281 |
| `leakage_only` | **0.8700** | [0.8557, 0.8845] | 0.9184 | 0.1296 | 1.0856 | 0.0650 |
| `esg_only` | 0.6699 | [0.6470, 0.6930] | 0.7954 | 0.2202 | 0.8147 | 0.1365 |
| `minimal_baseline` | 0.6345 | [0.6107, 0.6569] | 0.7736 | 0.2168 | 0.9561 | 0.0676 |

`no_closing_derived` equals `no_duration` because on this schema
`duration_months` is the only closing-date-derived column. They are kept as
separate variants because they are separate claims.

### The two deltas

```
current − no_duration    = +0.2559 ROC AUC
current − leakage_only   = +0.0465 ROC AUC
```

Normalising by discriminative ability above chance (`AUC − 0.5`, taking
`current` as 100%):

| variant | share of `current`'s above-chance ability |
|---|---|
| `leakage_only` (budget + duration_months) | **88.8%** |
| `esg_only` (no budget, no duration) | 40.8% |
| `no_duration` (everything except duration) | 38.6% |
| `minimal_baseline` (budget alone) | 32.3% |

## What this shows

**One column carries most of it.** Removing `duration_months` alone takes ROC
AUC from 0.917 to 0.661 — a drop of 0.256, with confidence intervals nowhere
near touching ([0.9057, 0.9282] against [0.6375, 0.6836]). Every other metric
moves the same way: PR AUC 0.951 → 0.784, Brier 0.099 → 0.220, log loss 0.361
→ 0.827, calibration error 0.023 → 0.128.

**Two columns reproduce nearly the whole thing.** `budget + duration_months`
alone reaches 0.870, within 0.047 of the full feature set, and accounts for
88.8% of its above-chance discriminative ability. The remaining five columns
add 0.047.

**What is left without the leak is a narrow band.** `no_duration` (0.6605),
`esg_only` (0.6699) and `minimal_baseline` (0.6345) sit within ~0.035 of each
other, and their intervals overlap. Budget alone gets 0.6345; adding
`co2_reduction`, `social_impact`, `country_gdp_per_capita`, `category` and
`region` gets 0.6699.

**`social_impact` behaves as the code said it would.** `esg_only` excludes
`budget` entirely and still scores *above* `minimal_baseline`, which is
budget alone. `social_impact` is synthesized from `log(budget)`
(`fetch_wb_projects.py`'s own docstring), so budget's signal is present in
the "ESG-only" set through it. The earlier audit predicted this by reading
the code; this is the measurement.

**`leakage_only` ranks well and is calibrated badly.** It has the second-best
ROC AUC (0.870) and the *worst* log loss of all six (1.086). Ranking ability
and probability quality come apart here, which is worth remembering wherever
a single AUC is quoted as "the" measure.

## What this does not show

- **Not a reproduction of 0.905.** `current` = 0.9165 is close, but it is a
  different estimator on a different split from a different code path. It is
  its own number, and the closeness only establishes that this measurement is
  examining the same regime as the claim.
- **Not proof that the served model is bad.** This measures the dataset's
  predictability and where it comes from, not the behaviour of a particular
  deployed artefact.
- **Not a clean measurement.** `legacy-approximate` name-grouping cannot rule
  out one project appearing on both sides. The same run on a file carrying
  `source_project_id` (the #223 schema) is the stronger version and has not
  been done.
- **Not a verdict on what `success` should be.** The ablation says how much
  of the current target is recoverable from the way it was built. It cannot
  say what the target ought to measure — that is the domain question, and it
  comes first.

## What follows

Nothing here changed the model, the dataset or production: no retrain, no
registration, no activation, no deployment. The harness refuses to write
under `models/` or `data/` and wrote one JSON outside both.

For #232 this settles the magnitude and leaves the issue open:

1. the domain definition of `success` and its ground truth — owner's call,
   and it precedes any redesign;
2. the same ablation on an id-carrying dataset, for evidence that does not
   rest on name-grouping;
3. a decision on whether `duration_months` can be re-derived (real dates
   only, with an explicit missing-indicator instead of a budget-shaped
   fallback) or has to be dropped.

Until then the 0.905 figure should not be quoted as evidence of model
quality, and no retrain or promotion should proceed on it.
