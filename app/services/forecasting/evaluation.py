"""Rolling-origin evaluation: the only way a forecast claim here may be made (#199 phase 6).

`baselines.py` gave models something to lose to. This gives them somewhere to
lose it. Measured 2026-08-16: nothing in this package computed MASE, interval
coverage or a confidence interval on a metric -- `tests/test_forecast_metrics.py`
covers a database table that *stores* metrics and the endpoints that read it,
never their computation.

## Why rolling origin and not a holdout

A single holdout gives one number from one accident of where the split landed.
The §7 gate in `entry_conditions.py` requires twelve windows for exactly this
reason, and the M3 declaration commits to it. Each window here trains on data
strictly before its test period and is scored on the horizon that follows, so
the arrangement is the one the gate will be argued from rather than a friendlier
one chosen afterwards.

## MASE, and the mistake it invites

MASE divides by the mean absolute error of a seasonal-naive one-step forecast
**computed on the training data of that window**. Scaling by the test set
instead is the classic error: it lets the denominator move with the thing being
measured, so a model looks better precisely where the test period happens to be
volatile. `_seasonal_scale` takes the training slice and nothing else, and a
test asserts that a model scored against a deliberately volatile test period
keeps the same MASE.

MASE < 1 means the model beat seasonal naive over that window. That is the
number principle 14 is about.

## Regions

`evaluate_by_region` runs the whole thing once per region and reports the worst
one beside the mean, because a model that is fine on average and three times
worse in one region is not a model to promote.

A region with too little data is recorded in `skipped` with its reason, never
dropped. The regions that fall out are the ones with the least data, which are
usually the hard ones, so silently omitting them moves the worst-region figure
in the flattering direction every single time.
"""
import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd


@dataclass
class WindowResult:
    """One origin: what was trained on, what was scored, and how it did."""

    window: int
    train_end: int
    test_start: int
    test_end: int
    mae: float
    rmse: float
    mase: float
    bias: float
    interval_coverage: Optional[float]


@dataclass
class EvaluationReport:
    """Every window kept, not only the average.

    The mean alone hides the case the gate cares about -- a model that is
    excellent in ten windows and catastrophic in two is not a model to promote,
    and averaging says it is fine.
    """

    model_name: str
    horizon: int
    windows: List[WindowResult] = field(default_factory=list)

    def _values(self, metric: str) -> np.ndarray:
        return np.array(
            [getattr(w, metric) for w in self.windows if getattr(w, metric) is not None],
            dtype=float,
        )

    def mean(self, metric: str = "mae") -> float:
        values = self._values(metric)
        return float(values.mean()) if values.size else float("nan")

    def worst(self, metric: str = "mae") -> float:
        values = self._values(metric)
        return float(values.max()) if values.size else float("nan")

    def confidence_interval(self, metric: str = "mae", level: float = 0.95,
                            resamples: int = 2000, seed: int = 0) -> Dict[str, float]:
        """Bootstrap CI over the per-window values.

        Twelve windows is a small sample, and reporting a mean from twelve
        numbers as though it were precise is how 0.81 on 171 rows came to be
        treated as clearing 0.80. The interval is over windows, so it answers
        "how much would this move on a different twelve" -- not "how much would
        it move on different data", which needs more than this.
        """
        values = self._values(metric)
        if values.size < 2:
            return {"low": float("nan"), "high": float("nan"), "n_windows": float(values.size)}

        rng = np.random.default_rng(seed)
        means = np.array([
            rng.choice(values, size=values.size, replace=True).mean()
            for _ in range(resamples)
        ])
        tail = (1.0 - level) / 2.0
        return {
            "low": float(np.quantile(means, tail)),
            "high": float(np.quantile(means, 1.0 - tail)),
            "n_windows": float(values.size),
        }

    def beats(self, other: "EvaluationReport", metric: str = "mae") -> bool:
        """Lower is better for every metric here. Ties are not wins."""
        mine, theirs = self.mean(metric), other.mean(metric)
        if math.isnan(mine) or math.isnan(theirs):
            return False
        return mine < theirs


def _seasonal_scale(train: np.ndarray, season_length: int) -> float:
    """Mean absolute seasonal-naive one-step error over the TRAINING data.

    The denominator must not see the test period; see the module docstring.
    """
    if train.size <= season_length:
        raise ValueError(
            f"need more than {season_length} training points to scale MASE, got {train.size}"
        )
    diffs = np.abs(train[season_length:] - train[:-season_length])
    scale = float(diffs.mean())
    if scale == 0.0:
        # A perfectly periodic training slice. Dividing by it would produce inf
        # or nan and quietly poison every average downstream.
        raise ValueError("seasonal scale is zero: the training slice is perfectly periodic")
    return scale


def rolling_origin_windows(n_observations: int, train_size: int, horizon: int,
                           n_windows: int, step: Optional[int] = None) -> List[Dict[str, int]]:
    """Index splits where every test period follows its own training data.

    `step` defaults to the horizon, so windows do not overlap and each test
    point is scored once. Raises rather than silently returning fewer windows
    than asked for: a report over eight windows labelled twelve is the kind of
    quiet shortfall the §7 gate exists to prevent.
    """
    if train_size < 1 or horizon < 1 or n_windows < 1:
        raise ValueError("train_size, horizon and n_windows must all be >= 1")
    step = horizon if step is None else step
    if step < 1:
        raise ValueError(f"step must be >= 1, got {step}")

    needed = train_size + horizon + step * (n_windows - 1)
    if n_observations < needed:
        raise ValueError(
            f"{n_windows} windows at horizon {horizon} need {needed} observations, "
            f"have {n_observations}"
        )

    windows = []
    for index in range(n_windows):
        train_end = train_size + step * index
        windows.append({
            "window": index,
            "train_end": train_end,
            "test_start": train_end,
            "test_end": train_end + horizon,
        })
    return windows


def evaluate_rolling_origin(
    fit_predict: Callable[[pd.DataFrame, int], Sequence[float]],
    df: pd.DataFrame,
    target_col: str,
    horizon: int,
    train_size: int,
    n_windows: int,
    season_length: int = 7,
    model_name: str = "model",
    interval: Optional[Callable[[pd.DataFrame, int], Sequence[Sequence[float]]]] = None,
) -> EvaluationReport:
    """Score `fit_predict` over rolling origins.

    `fit_predict(train_df, horizon)` must return `horizon` point forecasts using
    only the rows it is given. That signature is the whole contract: a callable
    that reaches past its argument for future rows is the leakage this design
    cannot detect, and the point-in-time helpers in `app/services/point_in_time.py`
    exist for that half of the problem.
    """
    if "ds" not in df.columns or target_col not in df.columns:
        raise ValueError("df must carry 'ds' and the target column")

    frame = df[["ds", target_col]].copy()
    frame["ds"] = pd.to_datetime(frame["ds"])
    frame = frame.dropna(subset=[target_col]).sort_values("ds").reset_index(drop=True)
    values = pd.to_numeric(frame[target_col], errors="coerce").to_numpy(dtype=float)

    report = EvaluationReport(model_name=model_name, horizon=horizon)
    for split in rolling_origin_windows(len(frame), train_size, horizon, n_windows):
        train_df = frame.iloc[:split["train_end"]]
        actual = values[split["test_start"]:split["test_end"]]

        predicted = np.asarray(list(fit_predict(train_df, horizon)), dtype=float)
        if predicted.shape != actual.shape:
            raise ValueError(
                f"window {split['window']}: expected {actual.shape[0]} forecasts, "
                f"got {predicted.shape[0]}"
            )

        error = actual - predicted
        scale = _seasonal_scale(values[:split["train_end"]], season_length)

        coverage = None
        if interval is not None:
            bounds = np.asarray(list(interval(train_df, horizon)), dtype=float)
            if bounds.shape != (horizon, 2):
                raise ValueError(
                    f"window {split['window']}: interval must be {horizon}x2, got {bounds.shape}"
                )
            inside = (actual >= bounds[:, 0]) & (actual <= bounds[:, 1])
            coverage = float(inside.mean())

        report.windows.append(WindowResult(
            window=split["window"],
            train_end=split["train_end"],
            test_start=split["test_start"],
            test_end=split["test_end"],
            mae=float(np.mean(np.abs(error))),
            rmse=float(math.sqrt(np.mean(error ** 2))),
            mase=float(np.mean(np.abs(error)) / scale),
            # Signed on purpose: a model that is wrong by 5 in both directions
            # and one that is always 5 low have the same MAE and are different
            # problems.
            bias=float(np.mean(error)),
            interval_coverage=coverage,
        ))

    return report


@dataclass
class RegionalReport:
    """Per-region evaluation, with what was left out kept beside it.

    The worst region is the number that decides whether a model is deployable:
    a mean that hides one region at three times the error is describing a model
    nobody should promote. But a worst-region figure computed over regions that
    silently dropped out is worse than none, because the regions most likely to
    drop out are the ones with the least data -- which are usually the hard ones.
    So `skipped` travels with the result and carries the reason.
    """

    metric: str
    reports: Dict[str, EvaluationReport] = field(default_factory=dict)
    skipped: Dict[str, str] = field(default_factory=dict)

    def scored(self) -> Dict[str, float]:
        return {name: r.mean(self.metric) for name, r in self.reports.items()}

    def worst_region(self):
        """(region, value) for the highest mean, or (None, nan) if nothing scored."""
        scored = {k: v for k, v in self.scored().items() if not math.isnan(v)}
        if not scored:
            return None, float("nan")
        name = max(scored, key=scored.get)
        return name, scored[name]

    def best_region(self):
        scored = {k: v for k, v in self.scored().items() if not math.isnan(v)}
        if not scored:
            return None, float("nan")
        name = min(scored, key=scored.get)
        return name, scored[name]

    def spread(self) -> float:
        """Worst minus best. A model even across regions has a small one."""
        _, worst = self.worst_region()
        _, best = self.best_region()
        if math.isnan(worst) or math.isnan(best):
            return float("nan")
        return worst - best

    def coverage(self) -> Dict[str, float]:
        """How much of the region set was actually scored.

        Reported so a reader can tell "no region did badly" from "few regions
        were looked at".
        """
        total = len(self.reports) + len(self.skipped)
        return {
            "regions_scored": float(len(self.reports)),
            "regions_skipped": float(len(self.skipped)),
            "regions_total": float(total),
        }


def evaluate_by_region(
    fit_predict: Callable[[pd.DataFrame, int], Sequence[float]],
    df: pd.DataFrame,
    target_col: str,
    region_col: str,
    horizon: int,
    train_size: int,
    n_windows: int,
    season_length: int = 7,
    metric: str = "mase",
) -> RegionalReport:
    """Run the rolling-origin evaluation once per region.

    A region that cannot supply enough observations is recorded in `skipped`
    with the reason rather than omitted. Dropping it would move the worst-region
    figure in the flattering direction every time.
    """
    if region_col not in df.columns:
        raise ValueError(f"df has no column {region_col!r}")

    report = RegionalReport(metric=metric)
    for region, group in df.groupby(region_col, sort=True):
        try:
            report.reports[str(region)] = evaluate_rolling_origin(
                fit_predict, group, target_col, horizon=horizon,
                train_size=train_size, n_windows=n_windows,
                season_length=season_length, model_name=str(region),
            )
        except ValueError as exc:
            report.skipped[str(region)] = str(exc)
    return report


@dataclass
class Comparison:
    """A candidate against every baseline, over identical windows.

    Engineering principle 14 -- every model must beat a simple baseline -- had
    no function that answers whether it does. `baselines.py` supplied the
    opponents and `evaluate_rolling_origin` the arena; this is the verdict.

    `verdict` is `beats_all`, `loses_to_some` or `not_comparable`. The third is
    not a formality: a candidate that could not be scored at all must never read
    as a win, and the losing baselines are named so the answer to "which one"
    does not require re-running anything.
    """

    metric: str
    candidate: EvaluationReport
    baselines: Dict[str, EvaluationReport] = field(default_factory=dict)
    skipped: Dict[str, str] = field(default_factory=dict)

    def losses(self) -> List[str]:
        """Baselines the candidate did not beat. Ties count as losses."""
        beaten = []
        for name, report in self.baselines.items():
            if not self.candidate.beats(report, self.metric):
                beaten.append(name)
        return sorted(beaten)

    def hardest(self):
        """(name, value) of the baseline that scored best -- the one to beat."""
        scored = {n: r.mean(self.metric) for n, r in self.baselines.items()}
        scored = {n: v for n, v in scored.items() if not math.isnan(v)}
        if not scored:
            return None, float("nan")
        name = min(scored, key=scored.get)
        return name, scored[name]

    @property
    def verdict(self) -> str:
        if math.isnan(self.candidate.mean(self.metric)) or not self.baselines:
            return "not_comparable"
        return "beats_all" if not self.losses() else "loses_to_some"

    def summary(self) -> Dict[str, object]:
        """What a report or a gate reads. Every figure it needs, and no prose."""
        hardest_name, hardest_value = self.hardest()
        return {
            "metric": self.metric,
            "verdict": self.verdict,
            "candidate": self.candidate.mean(self.metric),
            "candidate_worst_window": self.candidate.worst(self.metric),
            "candidate_ci": self.candidate.confidence_interval(self.metric),
            "hardest_baseline": hardest_name,
            "hardest_baseline_score": hardest_value,
            "lost_to": self.losses(),
            #: Baselines that could not be scored, and why. Reported beside the
            #: verdict because "beat every baseline" over a set that quietly
            #: shrank is not the same claim.
            "baselines_skipped": dict(self.skipped),
            "baselines_scored": len(self.baselines),
        }


def compare_to_baselines(
    fit_predict: Callable[[pd.DataFrame, int], Sequence[float]],
    df: pd.DataFrame,
    target_col: str,
    horizon: int,
    train_size: int,
    n_windows: int,
    season_length: int = 7,
    metric: str = "mase",
    candidate_name: str = "candidate",
    baselines=None,
) -> Comparison:
    """Score a candidate and every baseline over the *same* windows.

    Identical windows is the whole point. Comparing a model scored on one split
    against a baseline scored on another compares two arrangements, not two
    models, and the difference can be larger than the effect being claimed.

    A baseline that cannot be scored on this series is recorded in `skipped`,
    never dropped: the ones that fall out are the ones needing the most history,
    and seasonal naive -- the hardest to beat -- needs more than the others.
    """
    from app.services.forecasting.baselines import default_baselines

    candidate = evaluate_rolling_origin(
        fit_predict, df, target_col, horizon=horizon, train_size=train_size,
        n_windows=n_windows, season_length=season_length, model_name=candidate_name,
    )
    comparison = Comparison(metric=metric, candidate=candidate)

    for baseline in (baselines if baselines is not None
                     else default_baselines(season_length=season_length)):
        def scored(train_df, h, model=baseline):
            model.fit(train_df, target_col)
            return model.predict(h).yhat

        try:
            comparison.baselines[baseline.model_name] = evaluate_rolling_origin(
                scored, df, target_col, horizon=horizon, train_size=train_size,
                n_windows=n_windows, season_length=season_length,
                model_name=baseline.model_name,
            )
        except ValueError as exc:
            comparison.skipped[baseline.model_name] = str(exc)

    return comparison
