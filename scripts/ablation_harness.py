#!/usr/bin/env python3
"""Measure what survives when the contested features are removed (#232).

`duration_months` and `success` are both derived from one fact -- whether a
project has a usable closing date -- and a two-feature rule reaches 88.75%
on a fresh fetch and 85.47% on the file the serving model trained on
(`docs/DATASET_AUDIT_2026-09-04_LIVE_FETCH.md`). That does not prove the
reported ROC AUC 0.905 is fictitious. It does mean the figure cannot serve
as evidence of quality until the leakage is removed or *quantified*, and
quantifying it needs an instrument: fit the same way on the same split,
vary only which features are allowed, and compare.

This is that instrument. It is deliberately **not** a retrain:

  * it never writes to `models/`, never registers anything in MLflow and
    never activates anything -- `assert_output_is_not_protected()` refuses
    an output path under `models/` or `data/` mechanically, rather than
    this paragraph promising it;
  * it writes exactly one artefact, a JSON report;
  * the models it fits live in memory for the length of one variant and are
    thrown away. Absolute performance is not the output. The *difference
    between variants, measured identically*, is.

## Why it refuses so much

Every guard here exists because the alternative is a number that looks like
a measurement and is not one. A split that puts one project in both train
and test reports memorisation as skill. A feature list that still contains
the target reports 1.0. A confidence interval computed on 8 rows is
decoration. The audit that produced #232 found four separate columns whose
names promised more than they carried; an instrument built to check that
claim must fail loudly rather than quietly produce the shape of an answer.

So the refusals are the contract, and `tests/test_ablation_harness.py`
mutation-tests them: removing the overlap check, swapping the grouped split
for a random row split, letting a target-derived feature into the ESG-only
group, or mis-computing the interval each turn a test red.

## What it cannot do on this schema

`--split temporal` refuses. `data/projects.csv` and the live-fetch output
carry no date column at all (budget, co2_reduction, social_impact,
duration_months, category, region, country_gdp_per_capita, success, name,
source [, source_project_id]) -- so a temporal split cannot be built
without inventing an ordering. Refusing is the honest answer; the
alternative is an ordering nobody chose being read as chronology.

Usage (see docs/ABLATION_HARNESS.md):

    python3 scripts/ablation_harness.py \
        --data data/projects.csv --out out/ablation.json --legacy-approximate
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dataset_identity import content_sha256  # noqa: E402

SCHEMA_VERSION = "ablation-report/1"

TARGET = "success"
ID_COLUMN = "source_project_id"


# --- refusals -------------------------------------------------------------
#
# One class per refusal, so a test can name the one it means. A single
# generic error would let a test pass on the wrong refusal.

class AblationRefusal(RuntimeError):
    """Base: the harness will not produce a number it cannot stand behind."""


class MissingProjectId(AblationRefusal): pass
class SplitOverlap(AblationRefusal): pass
class TargetInFeatures(AblationRefusal): pass
class FeatureIsTargetCopy(AblationRefusal): pass
class MissingFeature(AblationRefusal): pass
class SingleClass(AblationRefusal): pass
class SplitTooSmall(AblationRefusal): pass
class DatasetHashMismatch(AblationRefusal): pass
class ResultExists(AblationRefusal): pass
class NoTemporalColumn(AblationRefusal): pass
class TargetDerivedFeatureInCleanGroup(AblationRefusal): pass
class ProtectedOutputPath(AblationRefusal): pass


# --- what each column is, stated once -------------------------------------

@dataclass(frozen=True)
class Feature:
    """`target_derived` is the load-bearing field.

    It marks a feature whose *value* is produced by the same code path that
    decides the label, so including it in a group meant to exclude leakage
    is a contradiction rather than a choice. `lineage` is descriptive and
    goes into the report; `target_derived` is enforced.
    """
    name: str
    lineage: str
    target_derived: bool
    why: str


FEATURES = {
    f.name: f for f in [
        Feature("budget", "observed", False,
                "totalamt from the World Bank API, the one unambiguously "
                "observed numeric column"),
        Feature("duration_months", "mixed", True,
                "real (closing - board) months when both dates are usable, "
                "otherwise synth_duration(budget, pid) -- and which branch "
                "fires is decided by the same usable-closing-date fact that "
                "map_success() reads. This is the leakage #232 is about."),
        Feature("co2_reduction", "constant-in-practice", False,
                "co2_emissions_reduced is absent in 0/16188 raw API rows and "
                "0/16203 worldbank rows of data/projects.csv; every value is "
                "the script's own default 0"),
        Feature("social_impact", "synthetic", False,
                "synthesized from log(budget) per fetch_wb_projects.py's own "
                "docstring -- the WB API has no social-impact metric. Not a "
                "leakage vector, but it doubles budget's influence"),
        Feature("country_gdp_per_capita", "observed-with-imputation", False,
                "NY.GDP.PCAP.CD via the WB indicator API, dataset median "
                "where the country did not resolve"),
        Feature("category", "derived", False,
                "sector/project_name keyword-mapped. In the legacy file this "
                "column was built before #231, when 41.1% of an unfiltered "
                "fetch defaulted to 'energy' with no keyword match"),
        Feature("region", "derived", False,
                "regionname mapped to 5 buckets, else Unknown -- a "
                "deterministic mapping of a real API field"),
    ]
}

CATEGORICAL = ("category", "region")


@dataclass(frozen=True)
class Variant:
    name: str
    features: tuple
    must_exclude_target_derived: bool
    why: str


VARIANTS = [
    Variant("current",
            ("budget", "duration_months", "co2_reduction", "social_impact",
             "country_gdp_per_capita", "category", "region"),
            False,
            "everything the dataset offers, unchanged -- the number every "
            "other row is compared against"),
    Variant("no_duration",
            ("budget", "co2_reduction", "social_impact",
             "country_gdp_per_capita", "category", "region"),
            False,
            "the single column #232 names. The gap between this and "
            "`current` is the quantity the issue asks for"),
    Variant("no_closing_derived",
            ("budget", "co2_reduction", "social_impact",
             "country_gdp_per_capita", "category", "region"),
            True,
            "every feature whose value depends on the closing date. On this "
            "schema that set is {duration_months} and nothing else, so the "
            "feature list equals no_duration -- kept as a separate variant "
            "because they are different claims, and a schema that later "
            "grows a second closing-derived column must change this one "
            "without touching no_duration"),
    Variant("leakage_only",
            ("budget", "duration_months"),
            False,
            "the two features the naive rule used. If this scores close to "
            "`current`, the rest of the columns are not what the model is "
            "reading"),
    Variant("esg_only",
            ("co2_reduction", "social_impact", "country_gdp_per_capita",
             "category", "region"),
            True,
            "the columns that nominally describe the project rather than its "
            "administrative state. Expected to be weak -- co2_reduction is a "
            "constant and social_impact is budget in disguise -- and that "
            "expectation is exactly what wants measuring rather than assuming"),
    Variant("minimal_baseline",
            ("budget",),
            False,
            "one observed column. Anything that does not beat this is not "
            "earning its complexity"),
]

VARIANTS_BY_NAME = {v.name: v for v in VARIANTS}


# --- guards ---------------------------------------------------------------

def assert_group_excludes_target_derived(variant: Variant) -> None:
    """A variant that claims to exclude leakage must actually exclude it."""
    if not variant.must_exclude_target_derived:
        return
    offenders = [n for n in variant.features
                 if n in FEATURES and FEATURES[n].target_derived]
    if offenders:
        raise TargetDerivedFeatureInCleanGroup(
            f"variant {variant.name!r} declares it excludes target-derived "
            f"features but lists {offenders}")


def assert_features_are_usable(df: pd.DataFrame, features: Sequence[str],
                               target: str = TARGET) -> None:
    if target in features:
        raise TargetInFeatures(f"{target!r} is listed as a feature")
    for name in features:
        if name not in df.columns:
            raise MissingFeature(f"feature {name!r} is not in the dataset")
    if target not in df.columns:
        raise MissingFeature(f"target {target!r} is not in the dataset")
    y = df[target]
    for name in features:
        # An exact copy of the target is not a feature, whatever it is
        # called. Compared as strings so 1 and "1" cannot slip past.
        if df[name].astype(str).equals(y.astype(str)):
            raise FeatureIsTargetCopy(
                f"feature {name!r} is an exact copy of the target")


def assert_both_classes(y: pd.Series, where: str) -> None:
    present = sorted(set(y.dropna().astype(str)))
    if len(present) < 2:
        raise SingleClass(f"{where}: only class(es) {present} present")


def assert_no_id_overlap(splits: dict, id_col: str = ID_COLUMN) -> None:
    """No project may appear in more than one part.

    This is the check that decides whether the reported metric is a
    measurement or a memory test, so it is a separate function that always
    runs, rather than a property the splitter is trusted to have.
    """
    seen = {}
    for part, frame in splits.items():
        if id_col not in frame.columns:
            raise MissingProjectId(
                f"{part}: no {id_col!r} column, so overlap cannot be checked")
        for value in frame[id_col].astype(str):
            if value in seen and seen[value] != part:
                raise SplitOverlap(
                    f"id {value!r} appears in both {seen[value]!r} and {part!r}")
            seen[value] = part


def assert_split_big_enough(splits: dict, min_rows: int) -> None:
    for part, frame in splits.items():
        if len(frame) < min_rows:
            raise SplitTooSmall(
                f"{part} has {len(frame)} rows, below the {min_rows} needed "
                f"for an interval worth quoting")


def assert_dataset_matches_manifest(data_path: str,
                                     manifest_path: Optional[str]) -> Optional[str]:
    """Returns the dataset's own sha256, and refuses a manifest that disagrees."""
    actual = content_sha256(data_path)
    if not manifest_path:
        return actual
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    expected = (manifest.get("transformed_sha256")
                or manifest.get("content_sha256")
                or manifest.get("sha256"))
    if expected and expected != actual:
        raise DatasetHashMismatch(
            f"{data_path} hashes {actual}, manifest says {expected}")
    return actual


def assert_output_is_not_protected(path: str) -> None:
    """The harness writes one JSON report and nothing else.

    Enforced rather than promised: an --out under models/ or data/ is
    refused, so a mistyped path cannot overwrite the serving model or the
    training file this work is forbidden from touching.
    """
    resolved = os.path.abspath(path)
    root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    for protected in ("models", "data"):
        guarded = os.path.join(root, protected) + os.sep
        if resolved.startswith(guarded):
            raise ProtectedOutputPath(
                f"refusing to write into {protected}/: {resolved}")


# --- splitting ------------------------------------------------------------

def _part_for_key(key: str, seed: int, fractions: Sequence) -> str:
    """Deterministic, grouped by construction.

    A hash of the key decides the part, so every row carrying the same key
    lands in the same part without needing a shuffle to be trusted, and the
    assignment is reproducible from the seed alone.
    """
    digest = hashlib.sha256(f"{seed}:{key}".encode("utf-8")).hexdigest()
    position = int(digest[:16], 16) / float(1 << 64)
    cumulative = 0.0
    for name, fraction in fractions:
        cumulative += fraction
        if position < cumulative:
            return name
    return fractions[-1][0]


DEFAULT_FRACTIONS = (("train", 0.6), ("validation", 0.2), ("test", 0.2))


def grouped_split(df: pd.DataFrame, id_col: str = ID_COLUMN, *, seed: int = 0,
                  fractions: Sequence = DEFAULT_FRACTIONS) -> dict:
    """Whole projects to one part each. Never rows independently."""
    if id_col not in df.columns:
        raise MissingProjectId(
            f"{id_col!r} is absent, so rows cannot be grouped by project. "
            f"For the legacy file that never carried an id, pass "
            f"--legacy-approximate and read the result as weaker evidence.")
    ids = df[id_col].astype(str)
    if (ids.str.strip() == "").any():
        raise MissingProjectId(
            f"{id_col!r} is blank on at least one row; a blank id would "
            f"group unrelated projects together")
    assignment = ids.map(lambda key: _part_for_key(key, seed, fractions))
    return {name: df[assignment == name].copy() for name, _ in fractions}


def legacy_approximate_split(df: pd.DataFrame, *, seed: int = 0,
                             fractions: Sequence = DEFAULT_FRACTIONS) -> dict:
    """For the 17K file, which carries no project id at all.

    Groups by a normalised `name`, which is a *heuristic* and is labelled as
    one everywhere it surfaces. Two different projects sharing a name are
    grouped as if they were one; the same project recorded under two spellings
    is split. This is weaker evidence than `grouped`, and the report says so
    in a field rather than in a footnote.
    """
    if "name" not in df.columns:
        raise MissingProjectId(
            "legacy-approximate needs a `name` column to group on")
    key = (df["name"].fillna("").astype(str)
           .str.lower().str.replace(r"\s+", " ", regex=True).str.strip())
    assignment = key.map(lambda k: _part_for_key(k, seed, fractions))
    out = {name: df[assignment == name].copy() for name, _ in fractions}
    for frame in out.values():
        frame["_approx_group_key"] = key[frame.index]
    return out


def temporal_split(df: pd.DataFrame, date_col: Optional[str] = None, **_kw) -> dict:
    """Refuses on this schema, deliberately.

    Neither `data/projects.csv` nor the live-fetch output carries a date
    column. A temporal split needs an ordering the data does not contain,
    and manufacturing one -- row order, or a date reconstructed from
    duration_months, which is itself the contested column -- would produce a
    number shaped like a temporal validation that is not one.
    """
    if not date_col or date_col not in df.columns:
        raise NoTemporalColumn(
            "this schema carries no date column, so a temporal split cannot "
            "be built without inventing an ordering. Use --split grouped.")
    ordered = df.sort_values(date_col)
    n = len(ordered)
    return {
        "train": ordered.iloc[: int(n * 0.6)].copy(),
        "validation": ordered.iloc[int(n * 0.6): int(n * 0.8)].copy(),
        "test": ordered.iloc[int(n * 0.8):].copy(),
    }


# --- metrics --------------------------------------------------------------

def expected_calibration_error(y_true: np.ndarray, y_prob: np.ndarray,
                               n_bins: int = 10) -> float:
    """Equal-width bins on the predicted probability; |accuracy - confidence|
    weighted by bin population. Bins are equal-width rather than equal-count
    so the number is comparable across variants that concentrate their
    predictions differently."""
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    index = np.clip(np.digitize(y_prob, edges[1:-1], right=False), 0, n_bins - 1)
    total = len(y_true)
    error = 0.0
    for b in range(n_bins):
        mask = index == b
        if not mask.any():
            continue
        error += (mask.sum() / total) * abs(y_true[mask].mean() - y_prob[mask].mean())
    return float(error)


def bootstrap_ci(values: Sequence[float], alpha: float = 0.05) -> tuple:
    """The (lo, hi) percentile interval of already-computed bootstrap values.

    Split out as a pure function precisely so the arithmetic can be checked
    against hand-computed input rather than inferred from a model's output.
    """
    if not len(values):
        return (None, None)
    arr = np.asarray(values, dtype=float)
    lo = float(np.percentile(arr, 100.0 * (alpha / 2.0)))
    hi = float(np.percentile(arr, 100.0 * (1.0 - alpha / 2.0)))
    return (lo, hi)


def _safe(fn: Callable, *args) -> Optional[float]:
    try:
        return float(fn(*args))
    except Exception:
        return None


def evaluate_predictions(y_true: np.ndarray, y_prob: np.ndarray, *,
                         threshold: float) -> dict:
    """`threshold` is required, never inferred.

    accuracy and F1 depend entirely on where the cut is put, so a harness
    that chose one silently would be reporting its own choice as a property
    of the model."""
    from sklearn.metrics import (accuracy_score, average_precision_score,
                                 brier_score_loss, f1_score, log_loss,
                                 roc_auc_score)
    y_pred = (y_prob >= threshold).astype(int)
    return {
        "roc_auc": _safe(roc_auc_score, y_true, y_prob),
        "pr_auc": _safe(average_precision_score, y_true, y_prob),
        "log_loss": _safe(lambda a, b: log_loss(a, b, labels=[0, 1]), y_true, y_prob),
        "brier": _safe(brier_score_loss, y_true, y_prob),
        "calibration_error": expected_calibration_error(y_true, y_prob),
        "threshold": threshold,
        "accuracy_at_threshold": _safe(accuracy_score, y_true, y_pred),
        "f1_at_threshold": _safe(f1_score, y_true, y_pred),
    }


def resample_indices(rng, n: int, groups: Optional[np.ndarray]) -> np.ndarray:
    """Row bootstrap when there are no groups; cluster bootstrap when there are.

    With `groups`, whole clusters are drawn with replacement and every member
    of a drawn cluster comes with it. That is the point: rows inside one
    project are not independent draws, and resampling them as if they were
    reports an interval narrower than the data supports. On a dataset where
    each project contributes several rows, the row bootstrap counts the same
    project's agreement with itself as evidence.

    A cluster drawn twice contributes its rows twice, so the resample is
    about `n` rows only when cluster sizes are even; that is inherent to the
    method, not a defect, and `n_resamples_used` in the report is what says
    how many resamples actually produced a metric.
    """
    if groups is None:
        return rng.integers(0, n, n)
    _uniq, inverse = np.unique(groups, return_inverse=True)
    members = [np.flatnonzero(inverse == i) for i in range(inverse.max() + 1)]
    if not members:
        return np.array([], dtype=int)
    picked = rng.integers(0, len(members), len(members))
    return np.concatenate([members[i] for i in picked])


def bootstrap_metrics(y_true: np.ndarray, y_prob: np.ndarray, *,
                      threshold: float, n_boot: int, seed: int,
                      alpha: float = 0.05,
                      groups: Optional[np.ndarray] = None) -> dict:
    rng = np.random.default_rng(seed)
    n = len(y_true)
    collected: dict = {}
    for _ in range(max(0, n_boot)):
        idx = resample_indices(rng, n, groups)
        if len(idx) == 0:
            continue
        yt, yp = y_true[idx], y_prob[idx]
        if len(set(yt.tolist())) < 2:
            continue  # a resample with one class cannot produce an AUC
        for key, value in evaluate_predictions(yt, yp, threshold=threshold).items():
            if key == "threshold" or value is None:
                continue
            collected.setdefault(key, []).append(value)
    return {key: {"ci_lo": bootstrap_ci(vals, alpha)[0],
                  "ci_hi": bootstrap_ci(vals, alpha)[1],
                  "n_resamples_used": len(vals)}
            for key, vals in collected.items()}


# --- data description -----------------------------------------------------

def describe_columns(df: pd.DataFrame, columns: Iterable[str]) -> dict:
    """Missing means absent. A zero is a value.

    Stated as code because the first pass of the audit this harness serves
    got exactly this wrong: `is_missing()` treated "0" as missing and
    reported `success` as 30.34% absent when it was 0%.
    """
    out = {}
    for name in columns:
        if name not in df.columns:
            continue
        col = df[name]
        blank = col.isna() | (col.astype(str).str.strip() == "")
        numeric = pd.to_numeric(col, errors="coerce")
        out[name] = {
            "n_missing": int(blank.sum()),
            "n_zero": int((numeric == 0).sum()),
            "n_distinct": int(col.nunique(dropna=True)),
            "is_constant": bool(col.nunique(dropna=True) <= 1),
            "lineage": FEATURES[name].lineage if name in FEATURES else None,
            "target_derived": (FEATURES[name].target_derived
                               if name in FEATURES else None),
        }
    return out


# --- running one variant --------------------------------------------------

def _default_estimator():
    from sklearn.ensemble import RandomForestClassifier
    return RandomForestClassifier(n_estimators=200, random_state=0, n_jobs=-1)


def _encode(train: pd.DataFrame, other: pd.DataFrame, features: Sequence[str]):
    """Categoricals encoded from the training part only; unseen -> -1.

    Fitting the encoding on the whole frame would let the test part decide
    the codes, which is a small leak of exactly the kind this harness exists
    to measure.
    """
    tr = train[list(features)].copy()
    ot = other[list(features)].copy()
    for name in features:
        if name in CATEGORICAL:
            codes = {v: i for i, v in enumerate(sorted(tr[name].astype(str).unique()))}
            tr[name] = tr[name].astype(str).map(codes).fillna(-1).astype(int)
            ot[name] = ot[name].astype(str).map(codes).fillna(-1).astype(int)
        else:
            tr[name] = pd.to_numeric(tr[name], errors="coerce").fillna(0.0)
            ot[name] = pd.to_numeric(ot[name], errors="coerce").fillna(0.0)
    return tr, ot


def run_variant(variant: Variant, splits: dict, *, threshold: float,
                n_boot: int, seed: int,
                estimator_factory: Callable = _default_estimator,
                group_col: Optional[str] = None) -> dict:
    assert_group_excludes_target_derived(variant)
    train, test = splits["train"], splits["test"]
    for frame, where in ((train, "train"), (test, "test")):
        assert_features_are_usable(frame, variant.features)
        assert_both_classes(frame[TARGET], where)

    x_train, x_test = _encode(train, test, variant.features)
    y_train = pd.to_numeric(train[TARGET], errors="coerce").astype(int).to_numpy()
    y_test = pd.to_numeric(test[TARGET], errors="coerce").astype(int).to_numpy()

    model = estimator_factory()
    model.fit(x_train, y_train)
    y_prob = model.predict_proba(x_test)[:, 1]

    point = evaluate_predictions(y_test, y_prob, threshold=threshold)
    # Cluster bootstrap whenever the split had a grouping unit: rows inside
    # one project are not independent, and a row bootstrap over them reports
    # a narrower interval than the data supports.
    groups = (test[group_col].astype(str).to_numpy()
              if group_col and group_col in test.columns else None)
    return {
        "variant": variant.name,
        "why": variant.why,
        "features": list(variant.features),
        "excludes_target_derived": variant.must_exclude_target_derived,
        "metrics": point,
        "bootstrap": {
            "mode": "cluster" if groups is not None else "row",
            "unit": group_col if groups is not None else "row",
            "n_clusters": (int(len(set(groups.tolist()))) if groups is not None
                           else None),
        },
        "confidence_intervals_95": bootstrap_metrics(
            y_test, y_prob, threshold=threshold, n_boot=n_boot, seed=seed,
            groups=groups),
    }


# --- report ---------------------------------------------------------------

def _split_summary(splits: dict, id_col: Optional[str]) -> dict:
    out = {}
    for part, frame in splits.items():
        y = pd.to_numeric(frame[TARGET], errors="coerce")
        ids = (set(frame[id_col].astype(str)) if id_col and id_col in frame.columns
               else None)
        out[part] = {
            "rows": int(len(frame)),
            "positives": int((y == 1).sum()),
            "negatives": int((y == 0).sum()),
            "positive_rate": (float((y == 1).mean()) if len(frame) else None),
            "unique_project_ids": (len(ids) if ids is not None else None),
        }
    return out


def _overlap_report(splits: dict, id_col: Optional[str]) -> dict:
    """Reported as a number, not only enforced -- "must be zero" is only
    checkable by a reader if the count is in the artefact."""
    if not id_col:
        return {"checked": False, "overlapping_ids": None}
    sets = {p: set(f[id_col].astype(str)) for p, f in splits.items()
            if id_col in f.columns}
    overlaps = 0
    parts = list(sets)
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            overlaps += len(sets[parts[i]] & sets[parts[j]])
    return {"checked": True, "overlapping_ids": overlaps}


def write_result(path: str, payload: dict, *, force: bool = False) -> None:
    assert_output_is_not_protected(path)
    if os.path.exists(path) and not force:
        raise ResultExists(
            f"{path} exists; pass --force to overwrite. A silently replaced "
            f"run is how two different measurements come to share one name.")
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest", default=None,
                    help="refuse if the dataset's sha256 disagrees with it")
    ap.add_argument("--split", choices=("grouped", "temporal"), default="grouped")
    ap.add_argument("--legacy-approximate", action="store_true",
                    help="group by normalised name for the file that carries "
                         "no project id; the result is labelled weaker evidence")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--min-rows-per-split", type=int, default=200)
    ap.add_argument("--variants", default=",".join(v.name for v in VARIANTS))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    assert_output_is_not_protected(args.out)
    if os.path.exists(args.out) and not args.force:
        raise ResultExists(f"{args.out} exists; pass --force to overwrite")

    dataset_sha = assert_dataset_matches_manifest(args.data, args.manifest)
    df = pd.read_csv(args.data)

    if args.split == "temporal":
        splits = temporal_split(df)          # refuses on this schema
        split_mode, id_col, evidence = "temporal", None, "standard"
    elif args.legacy_approximate:
        splits = legacy_approximate_split(df, seed=args.seed)
        split_mode, id_col = "legacy-approximate", "_approx_group_key"
        evidence = "weaker: grouped by normalised name, not by project id"
    else:
        splits = grouped_split(df, seed=args.seed)
        split_mode, id_col, evidence = "grouped", ID_COLUMN, "standard"

    assert_no_id_overlap(splits, id_col)
    assert_split_big_enough(splits, args.min_rows_per_split)

    results = []
    for name in [n.strip() for n in args.variants.split(",") if n.strip()]:
        if name not in VARIANTS_BY_NAME:
            raise MissingFeature(f"unknown variant {name!r}")
        results.append(run_variant(
            VARIANTS_BY_NAME[name], splits, threshold=args.threshold,
            n_boot=args.bootstrap, seed=args.seed, group_col=id_col))

    payload = {
        "schema_version": SCHEMA_VERSION,
        "issue": "https://github.com/Evgeniy2001Poluhin/-SORA.Earth/issues/232",
        "dataset": {
            "path": args.data,
            "sha256": dataset_sha,
            "rows": int(len(df)),
            "columns": list(df.columns),
        },
        "split": {
            "mode": split_mode,
            "evidence_strength": evidence,
            "seed": args.seed,
            "fractions": {n: f for n, f in DEFAULT_FRACTIONS},
            "summary": _split_summary(splits, id_col),
            "id_overlap": _overlap_report(splits, id_col),
        },
        "columns": describe_columns(df, FEATURES.keys()),
        "settings": {
            "threshold": args.threshold,
            "bootstrap_resamples": args.bootstrap,
            "min_rows_per_split": args.min_rows_per_split,
            "estimator": "RandomForestClassifier(n_estimators=200, random_state=0)",
        },
        "variants": results,
        "not_claimed": [
            "This is not a retrain: nothing was written to models/, nothing "
            "was registered in MLflow, nothing was activated, and no "
            "promotion threshold was consulted.",
            "Absolute numbers here are a property of this estimator on this "
            "split, not of the serving model. The comparison between "
            "variants is the result; a single row of it is not.",
        ],
    }
    write_result(args.out, payload, force=args.force)
    print(f"wrote {args.out}")
    for row in results:
        auc = row["metrics"]["roc_auc"]
        ci = row["confidence_intervals_95"].get("roc_auc", {})
        print(f"  {row['variant']:20s} roc_auc={auc if auc is None else round(auc, 4)}"
              f"  95% CI=[{ci.get('ci_lo')}, {ci.get('ci_hi')}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
