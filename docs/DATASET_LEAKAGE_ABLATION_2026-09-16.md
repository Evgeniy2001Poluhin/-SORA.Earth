# duration_months leakage: a controlled ablation (#232)

Measured 2026-09-16. Reproduce:

```bash
python3 scripts/ablation_duration_leakage.py --data data/projects.csv
```

This quantifies what `docs/DATASET_AUDIT_2026-09-04_LIVE_FETCH.md` established
qualitatively: `duration_months` is built from the same closing-date fact that
`success` is built from, so it leaks the label. The audit gave a naive-rule
accuracy of **85.47%** on `data/projects.csv`
(`scripts/check_duration_success_leakage.py`). The question left open by #232
was *how much of the model's headline AUC that leakage accounts for*. This
answers it with a feature-set ablation, which does not depend on reconstructing
the synthetic-duration formula.

## Method

- **Data**: the `worldbank` rows of `data/projects.csv` — the file the serving
  RandomForest (`models/model.pkl`) was trained on. 16 203 rows → 16 110 after
  dropping 93 exact-duplicate rows and any NaN in the keys. Class balance:
  success=1 **69.5%**, success=0 **30.5%**.
- **Model**: `RandomForestClassifier(n_estimators=200)`, the serving model
  class, retrained per fold.
- **Estimate**: out-of-fold predictions from 5-fold `StratifiedKFold`; ROC-AUC,
  PR-AUC (average precision) and Brier score on the pooled OOF predictions, with
  1000-sample bootstrap 95% CIs.
- **Feature sets** (derived columns use the exact formulas in
  `app/main.py::make_features`):

  | set | columns |
  |---|---|
  | A `current` | the 9 model columns |
  | B `no_duration` | A minus `duration_months` (keeps `budget_per_month`) |
  | C `no_date_derived` | A minus `duration_months` **and** `budget_per_month` |
  | D `leakage_only` | `duration_months` + `budget_per_month` only |
  | E `permissible_esg` | `budget`, `co2_reduction`, `social_impact`, `country_gdp_per_capita`, `category`, `region` — nothing derived from a closing date |

## Result

| set | ROC-AUC (95% CI) | PR-AUC (95% CI) | Brier |
|---|---|---|---|
| A `current` | **0.868** [0.862, 0.874] | 0.923 [0.918, 0.928] | 0.130 |
| B `no_duration` | 0.863 [0.856, 0.869] | 0.919 [0.914, 0.924] | 0.134 |
| C `no_date_derived` | **0.632** [0.623, 0.640] | 0.777 [0.767, 0.786] | 0.211 |
| D `leakage_only` | **0.866** [0.859, 0.872] | 0.921 [0.915, 0.926] | 0.132 |
| E `permissible_esg` | 0.671 [0.662, 0.681] | 0.808 [0.799, 0.817] | 0.218 |

## What it means

1. **Two leakage features reproduce the whole model.** `duration_months` +
   `budget_per_month` alone (D) reach ROC-AUC **0.866**, statistically
   indistinguishable from the full 9-feature model (A, 0.868). The model's
   discrimination is the leakage.

2. **Dropping the raw column is not enough.** Removing `duration_months` but
   keeping `budget_per_month` (B) barely moves the number (0.868 → 0.863),
   because `budget_per_month = budget / duration_months` still carries the
   duration signal. Any mitigation has to remove the derived column too.

3. **Removing the closing-date-derived features collapses it.** With both gone
   (C), ROC-AUC falls to **0.632** — a drop of 0.24 — and Brier nearly doubles.
   The permissible-ESG-only ceiling (E) is **0.671**. That is the honest range
   of what the current features predict about `success` without reading the
   label off the closing date.

4. **The headline number is not quality evidence.** Whether the reported figure
   is 0.868 (this CV) or 0.905 (the previously reported validation), it lives
   almost entirely in features that are a restatement of the target. It cannot
   be used to compare models on promotion, and enlarging the dataset does not
   change it — the defect is structural, in how the features and the label are
   both derived from `closingdate`.

## Split caveat, stated plainly

`data/projects.csv` carries neither `source_project_id` nor any date, so the
grouped/temporal split #232 item 3 asks for **cannot be run on this file** — it
needs the live-fetch dataset (`scripts/fetch_wb_projects.py`, 16 076 rows with
exact ids and board/closing dates). What is here is stratified CV on a random
split. That direction of error favours the leakage: exact duplicates were
dropped, but budget/duration collisions between distinct projects remain, which
inflate every AUC above. A clean set (C, E) that still collapses to ~0.63–0.67
under a split that helps the model is a **lower bound** on the damage, not an
artefact.

A temporal split on the live-fetch data is the natural follow-up; it can only
widen the gap between the leakage sets and the clean sets, not close it.

## Recommendation against #232's checklist

- **Item 2 (controlled ablation)** — done, above. The leakage is isolated:
  ~0.63–0.67 is the legitimate ceiling; ~0.87–0.91 is leakage.
- **Item 1 (define `success`)** — the blocker. `success` is currently a
  derivative of project status and closing date, not an independent
  observation, and `duration_months` is another reading of the same fact. This
  is a product/ground-truth decision and is the owner's to make; the ablation
  says it must be made before any AUC is trusted.
- **Item 3 (grouped/temporal split)** — needs the live-fetch dataset; filed as
  the follow-up above. The offline ablation already answers the isolation
  question item 2 poses.

The retrain/promotion block in #232 should stay until item 1 is resolved: the
ablation confirms that promotion on this metric would reward leakage, not
quality.

## Resolution (2026-09): Option C — `success` is a heuristic, its AUC is not quality evidence

Three options were weighed for redefining `success`:

- **A** — an independent evaluation label (WB IEG outcome ratings): the correct
  label, but it needs a new data source + a join by project id (and
  `data/projects.csv` carries no `source_project_id`). **Deferred**, not rejected —
  this is the path to a metric that *can* be trusted.
- **B** — a disbursement-based label from the existing fetch: **measured
  infeasible.** `disbursement_percentage` is present in **0 / 4000** projects
  sampled evenly across the WB v2 projects API, so the label would be degenerate
  (all-zero). Disbursement data lives in a separate WB Finances API (new-source
  plumbing, and a weaker signal than outcome). Off the table.
- **C** — retire the quality claim. **Chosen.**

**Decision: C.** `success` stays a heuristic target. Its AUC (≈0.87–0.91) is
**leakage-inflated and is NOT validated evidence of model quality**: the ablation
above shows the two closing-date-derived features (`duration_months`,
`budget_per_month`) reproduce almost the whole score, and the label itself is
built from the same closing-date fact. Consequently:

- Do **not** present the classifier's AUC/accuracy as a quality metric, and do
  **not** use it as promotion evidence. The promotion gate's `MIN_AUC_THRESHOLD`
  is a floor/regression guard against a leaky metric, not proof of quality
  (annotated in `app/promotion.py`).
- The `success_probability` the product returns is a heuristic, not a validated
  prediction of real-world project success.
- Retrain/promotion stays gated on this label: no model can be called "better"
  by this AUC. A real, **independent** label — **Option A (WB IEG ratings)** — is
  the prerequisite for trusting any AUC, and is the deferred follow-up.

This resolves #232: item 2 (quantitative isolation) is done by the ablation;
item 1 (decide the target) is resolved as C with A deferred; item 3
(grouped/temporal split) is moot for a label we are explicitly not treating as
quality evidence.
