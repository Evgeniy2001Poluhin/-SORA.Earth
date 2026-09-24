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

from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

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
