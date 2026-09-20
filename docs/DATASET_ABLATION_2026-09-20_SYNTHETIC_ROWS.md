# What the success model reads, and what the synthetic rows cost

    measured      2026-09-20
    instrument    scripts/ablation_harness.py (#232), unchanged
    dataset       data/projects.csv, 17 071 rows
                  sha256 0e0faffb3aaebd0bddcb8da11ecc0e4ee044bb7f63376d79745093a08648afcd
    seed          0
    split         legacy-approximate — see "What this measurement is not"

## The question

`data/projects.csv` carries a `source` column, and 851 of its rows read
`synthetic`:

```
worldbank    16 203
synthetic       851
(absent)         17
```

Training does not filter on it — `_do_retrain` reads the file whole — so those
rows are in the serving model's training set. Engineering principle 9 allows
synthetic data "only in isolated tests and benchmarks", and this is neither.

The question asked was whether that matters. It does not, and answering it
produced a sharper number about something that does.

## Method

The project's own ablation harness, twice: once on the file as shipped, once on
the same file with `source == 'worldbank'` kept. Same seed, same split
construction, same variants. Only the rows differ, so the difference between the
columns is what the rows are worth.

## Result

ROC AUC, with the 95% bootstrap interval measured per variant rather than
summarised — they are not one width:

| variant | interval width, as shipped |
|---|---:|
| `current` | 0.0225 |
| `leakage_only` | 0.0288 |
| `no_closing_derived` | 0.0461 |
| `esg_only` | 0.0460 |
| `minimal_baseline` | 0.0463 |

This first read "intervals about 0.023 wide", which is the **half**-width of the
four wide ones and the full width of only `current`. The figure was stated as a
property of the whole table and was wrong for four of its five rows.

| variant | features | as shipped | World Bank only | Δ |
|---|---|---:|---:|---:|
| `current` | all seven | **0.9165** | 0.9156 | −0.0009 |
| `leakage_only` | `budget`, `duration_months` | **0.8700** | 0.8690 | −0.0010 |
| `no_closing_derived` | everything except closing-date-derived | **0.6605** | 0.6588 | −0.0017 |
| `esg_only` | `co2_reduction`, `social_impact`, `country_gdp_per_capita`, `category`, `region` | 0.6699 | 0.6669 | −0.0030 |
| `minimal_baseline` | `budget` alone | 0.6345 | 0.6317 | −0.0028 |

### The synthetic rows cost nothing measurable

Every difference is at most 0.003, an order of magnitude inside the intervals.
Removing the 851 rows is therefore free: it is a hygiene decision about
principle 9, not a modelling one, and nothing downstream has to be re-measured
when it happens.

### `duration_months` is very nearly the whole model

Read the left column as a ladder rather than as five separate numbers:

```
budget alone                                    0.6345
budget + duration_months                        0.8700     +0.2355
all seven features                              0.9165     +0.0465
all seven minus duration_months                 0.6605     -0.2560
the five that describe the project              0.6699
```

Two administrative columns reach 0.87. Adding the five that actually describe a
project — emissions avoided, social impact, the country's GDP per capita, the
category and the region — buys 0.046. Take `duration_months` away and the model
returns to roughly what `budget` alone achieves.

`duration_months` and `success` are two readings of one fact: whether the
project has a usable closing date. That is what #232 recorded and what it was
closed on (Option C, "retire the quality claim"). This measurement puts a figure
on it against the current file: **of the 0.9165 the model reports, about 0.24
is the leak and about 0.03 is the project's own attributes.**

## What follows for anyone presenting this

The number to avoid quoting is the one that looks best. "ROC AUC 0.92" describes
a model that has largely learned to tell closed projects from open ones, which
is not a prediction anybody needs. The honest statement of the same experiment
is that the project attributes carry roughly **0.67**, against **0.63** for
knowing the budget and nothing else.

That is not a failure of the platform. It is a property of a label derived from
administrative status, and it is why the correct label — an independent outcome
rating, World Bank IEG being the obvious candidate — is the prerequisite
recorded on #232 rather than an improvement to be scheduled after.

## What this measurement is not

Carried from the harness's own output, because it refuses to be read as more
than it is:

- **Not a retrain.** Nothing was written to `models/`, nothing registered in
  MLflow, nothing activated, no promotion threshold consulted.
- **Not a statement about the serving model.** These are absolute numbers for
  one estimator on one split. The comparison between variants is the result; a
  single cell of it is not.
- **Weaker evidence than it could be.** The harness refused a grouped split and
  said why: `data/projects.csv` carries no `source_project_id`, so rows cannot
  be grouped by project, and `--legacy-approximate` groups by normalised name
  instead. It reported zero overlapping ids on that basis. A file that carried
  the id would make this stronger, and the absence is itself worth recording.

  This first said the absence "is the same provenance gap #164 and #75 are
  about". It is not: both of those are about `country_indicator_history` —
  #75 about reading a past vintage, #164 about `as_of_date` being rewritten in
  place — and neither names `data/projects.csv` or `source_project_id`.
  Different table, different missing fact. The column is described in
  `docs/ABLATION_HARNESS.md` as "the #223 schema", after the merged PR that gave
  the fetch pipelines a source identity; **nothing open tracks giving
  `data/projects.csv` that column**, and the join it would allow is Option A of
  #232, which is closed with that option recorded as deferred.

Reproduce with:

```bash
python scripts/ablation_harness.py --data data/projects.csv \
  --out /tmp/abl_full.json --seed 0 --legacy-approximate
```
