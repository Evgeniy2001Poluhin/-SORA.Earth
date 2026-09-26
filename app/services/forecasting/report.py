"""One reproducible report over one snapshot -- phase 6's exit criterion.

`docs/DEVELOPMENT_ROADMAP.md` phase 6 asks for the baselines, rolling-origin,
MAE/MASE/bias/interval coverage/bootstrap CI and the worst window, and then for
**a report**. Every component existed and nothing assembled one: measured
2026-09-20, `evaluation.py` was imported by tests and by no script, endpoint or
job, so the phase read as done from the code while producing nothing it is
judged by.

Three things this does that computing numbers does not:

- **carries the §7 verdict.** The gate is executable. A report without it is one
  someone quotes in February as though the accumulation had finished.
- **states what it was computed from** -- the window, the coverage floor, the
  points, the days. That is what "reproducible" means in the criterion.
- **names the bar.** No candidate model exists for this target yet, so the
  content worth having today is which baseline is hardest and what it scores.

Deterministic by construction: the baselines are, and the bootstrap interval is
seeded (`EvaluationReport.confidence_interval(seed=0)`). Two runs over one
snapshot produce the same document apart from `generated_at`.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from . import entry_conditions
from .baselines import default_baselines
from .daily_target import (coverage_by_day, daily_observations, required_hours,
                           snapshot_digest)
from .evaluation import evaluate_rolling_origin

#: The horizons the declaration names. Both are reported because the gate
#: answers differently for each and a reader seeing one number cannot tell which.
HORIZONS: Tuple[int, ...] = (7, 30)


def build_report(
    rows: Iterable[Tuple[str, datetime, float]],
    target: str,
    horizons: Sequence[int] = HORIZONS,
    declared_points: Optional[Sequence[str]] = None,
    season_length: int = 7,
    candidates: Optional[Sequence[str]] = None,
    include_shadow: bool = False,
) -> Dict[str, object]:
    """Assemble the report from raw hourly samples.

    `rows` are `(point, event_time, value)` -- the same shape
    `daily_target.daily_observations` takes, because the daily series this scores
    must be the declared one rather than a second construction that could differ
    from §3.

    `declared_points` should be the point set the declaration fixes. Left None it
    is taken from the data, which makes §7's "every declared region present"
    condition **vacuously true** -- the report says which of the two happened, so
    a pass on that condition can be read correctly.

    `candidates`: which candidates to evaluate, by name. None means all non-shadow
    candidates. An empty list evaluates none. `include_shadow`: whether to include
    shadow models (role="shadow") in the evaluation.

    When the §7 entry conditions are NOT met, candidates are listed with
    `evaluated: false` and the same refusal reasons the report already gives. No
    numbers. When they ARE met, each candidate is evaluated with
    `compare_to_baselines` on the SAME windows the baselines use.
    """
    rows = list(rows)
    observations = daily_observations(rows)
    coverage = coverage_by_day(rows)

    points_in_data = sorted({o.region for o in observations})
    points_source = "data" if declared_points is None else "caller"
    declared = sorted(declared_points) if declared_points is not None else points_in_data

    days = sorted({o.period_end for o in observations})
    last_day = days[-1] if days else None

    report: Dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": target,
        "snapshot": {
            # Phase 7's exit criterion: every result ties to a source and a
            # snapshot. The counts below do not identify anything -- two runs
            # over different data can produce the same three numbers -- so the
            # digest is what makes this report reproducible rather than merely
            # repeatable. Paired with `required_hours_per_day`, which is the
            # other half: the series is a function of the rows and §3's floor.
            "digest": snapshot_digest(rows),
            "points": len(points_in_data),
            "days": len(days),
            "first_day": days[0].isoformat() if days else None,
            "last_day": last_day.isoformat() if last_day else None,
            "observations": len(observations),
            "required_hours_per_day": required_hours(),
            "declared_points": len(declared),
            "declared_points_source": points_source,
            # Reported beside the count because "no day fell short" and "no day
            # was examined" are different facts, and one number cannot say which.
            "point_days_below_floor": sum(
                1 for hours in coverage.values() if hours < required_hours()),
            "point_days_examined": len(coverage),
        },
        "gate": {},
        "baselines": {},
    }

    gate: Dict[str, object] = {}
    for horizon in horizons:
        if last_day is None:
            gate[str(horizon)] = {
                "passed": False,
                "conditions": [{
                    "name": "the candidate has observations at all",
                    "passed": False,
                    "detail": "no day met the coverage floor",
                }],
            }
            continue
        verdict = entry_conditions.evaluate(
            observations, declared_regions=declared,
            horizon=horizon, last_day=last_day,
        )
        gate[str(horizon)] = {
            "passed": verdict.passed,
            "conditions": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in verdict.conditions
            ],
        }
    report["gate"] = gate

    primary = int(horizons[0])
    train_size = entry_conditions.TRAINING_DAYS[primary]

    baselines: Dict[str, object] = {}
    for point in points_in_data:
        frame = pd.DataFrame(
            [{"ds": pd.Timestamp(o.period_end), "y": o.value}
             for o in observations if o.region == point]
        ).sort_values("ds").reset_index(drop=True)

        scored: Dict[str, object] = {}
        skipped: Dict[str, str] = {}
        for model in default_baselines(season_length=season_length):
            def fit_predict(train_df, h, _model=model):
                _model.fit(train_df, "y")
                return _model.predict(h).yhat

            try:
                scores = evaluate_rolling_origin(
                    fit_predict, frame, "y",
                    horizon=primary, train_size=train_size,
                    n_windows=entry_conditions.REQUIRED_WINDOWS,
                    season_length=season_length, model_name=model.model_name,
                )
            except ValueError as exc:
                # Recorded, never dropped. The baselines that fall out are the
                # ones needing the most history -- seasonal naive, the hardest
                # to beat, needs more than the others -- so a quietly shrinking
                # set makes "beat every baseline" an easier claim each time.
                skipped[model.model_name] = str(exc)
                continue

            scored[model.model_name] = {
                "mae": scores.mean("mae"),
                "mase": scores.mean("mase"),
                "bias": scores.mean("bias"),
                "worst_window": scores.worst("mase"),
                "ci_mase": scores.confidence_interval("mase"),
                "windows": len(scores.windows),
            }

        hardest = min(scored, key=lambda n: scored[n]["mase"]) if scored else None
        baselines[point] = {
            "scored": scored, "skipped": skipped, "hardest": hardest,
            "horizon": primary, "train_size": train_size,
        }
    report["baselines"] = baselines

    # Phase 9: Candidate models evaluation
    from .challengers import CANDIDATES
    from .evaluation import compare_to_baselines, evaluate_by_region

    # Determine which candidates to evaluate
    available = {c.name: c for c in CANDIDATES}
    if candidates is None:
        # Default: all non-shadow candidates
        selected = [c for c in CANDIDATES if c.role != "shadow"]
    else:
        selected = [available[name] for name in candidates if name in available]

    # Add shadow models if requested
    if include_shadow:
        selected = [c for c in CANDIDATES if c.name in [s.name for s in selected] or c.role == "shadow"]
    else:
        selected = [c for c in selected if c.role != "shadow"]

    candidates_section: Dict[str, object] = {}
    primary_gate = gate.get(str(primary), {})
    gate_passed = primary_gate.get("passed", False)

    for spec in selected:
        model = spec.factory()

        if not gate_passed or not any(b["scored"] for b in baselines.values()):
            # Gate not passed or no baseline could be scored: list with evaluated=false
            candidates_section[spec.name] = {
                "evaluated": False,
                "role": spec.role,
                "reason": (
                    "the section 7 gate has not passed" if not gate_passed
                    else "no baseline could be scored on this snapshot"
                ),
            }
            continue

        # Gate passed: evaluate the candidate
        # Aggregate data across all points
        all_obs = []
        for point in points_in_data:
            point_obs = [o for o in observations if o.region == point]
            for o in point_obs:
                all_obs.append({"ds": pd.Timestamp(o.period_end), "y": o.value, "region": point})

        full_frame = pd.DataFrame(all_obs).sort_values("ds").reset_index(drop=True)

        if len(full_frame) < train_size + primary:
            candidates_section[spec.name] = {
                "evaluated": False,
                "role": spec.role,
                "reason": f"insufficient data: {len(full_frame)} rows, need at least {train_size + primary}",
            }
            continue

        try:
            # Evaluate across regions
            regional = evaluate_by_region(
                lambda train_df, h, m=model: (m.fit(train_df, "y"), m.predict(h).yhat)[1],
                full_frame,
                target_col="y",
                region_col="region",
                horizon=primary,
                train_size=train_size,
                n_windows=entry_conditions.REQUIRED_WINDOWS,
                season_length=season_length,
            )

            # Also run compare_to_baselines for a single representative point
            # to get the full comparison
            first_point = points_in_data[0]
            point_frame = pd.DataFrame(
                [{"ds": pd.Timestamp(o.period_end), "y": o.value}
                 for o in observations if o.region == first_point]
            ).sort_values("ds").reset_index(drop=True)

            comparison = compare_to_baselines(
                lambda train_df, h, m=model: (m.fit(train_df, "y"), m.predict(h).yhat)[1],
                point_frame,
                target_col="y",
                horizon=primary,
                train_size=train_size,
                n_windows=entry_conditions.REQUIRED_WINDOWS,
                season_length=season_length,
                candidate_name=spec.name,
            )

            # Compute metrics (both MASE and MAE)
            worst_region, worst_mase = regional.worst_region()
            mean_mase = float(np.mean([r.mean("mase") for r in regional.reports.values()]))

            # MAE metrics for eligibility
            mean_mae = float(np.mean([r.mean("mae") for r in regional.reports.values()]))
            worst_region_mae_name, worst_mae = None, float("nan")
            mae_by_region = {name: r.mean("mae") for name, r in regional.reports.items()}
            if mae_by_region:
                worst_region_mae_name = max(mae_by_region, key=mae_by_region.get)
                worst_mae = mae_by_region[worst_region_mae_name]

            # Compute baselines' aggregate MAE across all regions (mean and worst)
            baseline_mean_maes = {}
            baseline_worst_maes = {}
            for point in points_in_data:
                if point in baselines and baselines[point]["scored"]:
                    point_baselines = baselines[point]["scored"]
                    for bl_name, bl_scores in point_baselines.items():
                        bl_mae = bl_scores.get("mae", float("inf"))
                        if bl_name not in baseline_mean_maes:
                            baseline_mean_maes[bl_name] = []
                            baseline_worst_maes[bl_name] = []
                        baseline_mean_maes[bl_name].append(bl_mae)
                        baseline_worst_maes[bl_name].append(bl_mae)

            # Average across regions for each baseline
            baseline_agg = {}
            for bl_name in baseline_mean_maes:
                baseline_agg[bl_name] = {
                    "mean": float(np.mean(baseline_mean_maes[bl_name])),
                    "worst": float(np.max(baseline_worst_maes[bl_name])),
                }

            # Beats every baseline: candidate's mean MAE below every baseline's mean MAE
            # AND candidate's worst-region MAE below every baseline's worst-region MAE
            beats_all = True
            per_baseline_margins = {}
            if baseline_agg:
                for bl_name, bl_metrics in baseline_agg.items():
                    bl_mean = bl_metrics["mean"]
                    bl_worst = bl_metrics["worst"]
                    margin = mean_mae / bl_mean if bl_mean > 0 else float("inf")
                    per_baseline_margins[bl_name] = round(margin, 4)
                    # Must beat on BOTH mean and worst
                    if mean_mae >= bl_mean or worst_mae >= bl_worst:
                        beats_all = False
            else:
                beats_all = False

            # Eligible for promotion: candidate role AND beats every baseline
            eligible = spec.role == "candidate" and beats_all

            candidates_section[spec.name] = {
                "evaluated": True,
                "role": spec.role,
                "mean_mase": round(mean_mase, 4),
                "mean_mae": round(mean_mae, 4),
                "worst_region": worst_region,
                "worst_region_mase": round(worst_mase, 4) if not math.isnan(worst_mase) else None,
                "worst_region_mae": round(worst_mae, 4) if not math.isnan(worst_mae) else None,
                "beats_every_baseline": beats_all,
                "per_baseline_mae_ratios": per_baseline_margins,
                "eligible_for_promotion": eligible,
                "regions_scored": len(regional.reports),
                "regions_skipped": len(regional.skipped),
                "comparison_summary": comparison.summary(),
            }

        except Exception as exc:
            candidates_section[spec.name] = {
                "evaluated": False,
                "role": spec.role,
                "reason": f"evaluation failed: {exc}",
            }

    report["candidates"] = candidates_section

    refusals: List[str] = []
    primary_gate = gate.get(str(primary), {})
    if not primary_gate.get("passed"):
        failed = [c["name"] for c in primary_gate.get("conditions", [])
                  if not c["passed"]]
        refusals.append(
            f"the section 7 gate refuses at h={primary}: {', '.join(failed)}")
    if not any(b["scored"] for b in baselines.values()):
        refusals.append("no baseline could be scored on this snapshot")
    if points_source == "data":
        refusals.append(
            "the point set was taken from the data, so 'every declared region "
            "present' passed vacuously")

    report["evidential"] = not refusals
    report["why_not_evidential"] = refusals
    return report
