"""M3-A evaluation on the archived Open-Meteo Historical Forecast series.

This is a pseudo-real-time evaluation (M2 protocol §2.2) on a fixed vintage of
the Open-Meteo Historical Forecast API, downloaded 2026-10-11. It is NOT the
live series production collects, and results are labelled accordingly.

The archive covers 2018-01-01 through 2026-10-10 for all 21 declared points.
It is close to what was recorded (median |difference| 0.09°C over 23 days of
comparison), but it is a reconstruction from a fixed vintage, not the recording
itself.

**What this evaluation does:**

- Loads gzipped JSON files from the archive directory (weather/<POINT>_<YEAR>.json.gz)
- Builds the daily target per M3 §3 / Amendment 1.3 (reusing daily_target.daily_observations)
- Refuses if any point-day falls below the coverage floor or any declared point is missing
- Evaluates baselines (Naive, SeasonalNaive, MovingAverage, Climatology) and candidates
  (linear_trend, xgboost_lag) over rolling-origin windows placed at the END of the series
- Each window trains on ALL days from 2018-01-01 to its origin (expanding)
- Reports per-point and macro (equal-weight mean across points) MAE, RMSE, MASE, bias
- Success per M3-A §9: beats_all (macro MAE < every baseline) AND paired bootstrap
  95% CI of (candidate - strongest baseline) entirely below zero

**This is preregistered:** docs/M3A_ARCHIVE_DECLARATION.md was committed before
the first run on this data. Every constant, baseline, candidate, window
placement rule, and success criterion was fixed before any result existed.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .baselines import ClimatologyForecast, default_baselines
from .daily_target import (
    coverage_by_day,
    daily_observations,
    required_hours,
    snapshot_digest,
)
from .evaluation import EvaluationReport, evaluate_rolling_origin

ARCHIVE_PERIOD_START = date(2018, 1, 1)
ARCHIVE_PERIOD_END = date(2026, 10, 10)

HORIZONS = {
    7: {"n_windows": 104, "evaluation_period_start": date(2024, 10, 13)},
    30: {"n_windows": 24, "evaluation_period_start": date(2024, 10, 21)},
}

RESULT_LABEL = "pseudo-real-time · archive vintage 2026-10-11 · not a real-time claim"
TARGET_NAME = "openmeteo-historical-forecast:temperature_2m"
DECLARATION = "docs/M3A_ARCHIVE_DECLARATION.md v1.0"


def load_archive_rows(
    archive_dir: str | Path,
) -> List[Tuple[str, datetime, float]]:
    """Load hourly temperature_2m from the weather archive.

    Archive layout: `weather/<POINT>_<YEAR>.json.gz`, each a gzipped Open-Meteo
    JSON response with `hourly.time` (ISO "YYYY-MM-DDTHH:MM", UTC) and
    `hourly.temperature_2m`.

    POINT codes include hyphens (RU-MOW, RU-SPE). None values are skipped.
    Rows are restricted to the declared period (2018-01-01..2026-10-10 inclusive).

    Returns:
        List of (point, event_time as tz-aware UTC datetime, value)
    """
    archive_path = Path(archive_dir)
    weather_dir = archive_path / "weather"
    if not weather_dir.exists():
        raise ValueError(f"archive weather directory not found: {weather_dir}")

    rows: List[Tuple[str, datetime, float]] = []

    for file_path in sorted(weather_dir.glob("*.json.gz")):
        stem = file_path.stem
        stem = stem.removesuffix(".json")
        parts = stem.rsplit("_", 1)
        if len(parts) != 2:
            continue
        point, year_str = parts

        try:
            with gzip.open(file_path, "rt", encoding="utf-8") as f:
                data = json.load(f)
        except (gzip.BadGzipFile, json.JSONDecodeError) as exc:
            raise ValueError(f"failed to read {file_path}: {exc}") from exc

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])

        if len(times) != len(temps):
            raise ValueError(
                f"{file_path}: time and temperature_2m lengths differ "
                f"({len(times)} vs {len(temps)})"
            )

        for time_str, temp in zip(times, temps):
            if temp is None:
                continue

            try:
                event_time = datetime.fromisoformat(time_str).replace(tzinfo=timezone.utc)
            except ValueError as exc:
                raise ValueError(f"{file_path}: invalid timestamp {time_str!r}") from exc

            event_date = event_time.date()
            if not (ARCHIVE_PERIOD_START <= event_date <= ARCHIVE_PERIOD_END):
                continue

            rows.append((point, event_time, float(temp)))

    return rows


def manifest_digest(archive_dir: str | Path) -> str:
    """SHA-256 over sorted (relative path, sha256 of file bytes) of weather files.

    This fingerprints the archive vintage. A fresh download is a new vintage:
    the provider may revise the archive, and results from two vintages are not
    compared as though they shared one.
    """
    archive_path = Path(archive_dir)
    weather_dir = archive_path / "weather"
    if not weather_dir.exists():
        raise ValueError(f"archive weather directory not found: {weather_dir}")

    file_hashes: List[Tuple[str, str]] = []
    for file_path in sorted(weather_dir.glob("*.json.gz")):
        rel_path = file_path.relative_to(archive_path).as_posix()
        with open(file_path, "rb") as f:
            file_hash = hashlib.sha256(f.read()).hexdigest()
        file_hashes.append((rel_path, file_hash))

    canonical = "\n".join(f"{path}\t{fhash}" for path, fhash in file_hashes)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evaluate_archive(
    archive_dir: str | Path,
    horizons: Optional[Sequence[int]] = None,
    window_configs: Optional[Dict[int, Dict]] = None,
) -> Dict[str, object]:
    """Run the preregistered M3-A evaluation on the archive.

    This is the evaluation logic, testable without network or database. The CLI
    script is a thin wrapper around it.

    Args:
        archive_dir: Path to the archive (contains weather/ subdirectory)
        horizons: Which horizons to evaluate (defaults to [7, 30])
        window_configs: Optional custom window configurations (defaults to HORIZONS).
                       Format: {horizon: {"n_windows": int, "evaluation_period_start": date}}

    Returns:
        A deterministic report dict (apart from generated_at timestamp) with:
        - label, declaration, target, manifest/snapshot digests, git commit
        - period, points, day counts
        - per-horizon: windows, train_size, first test day, per-model metrics
        - refusal details if entry conditions not met
        - success verdicts per candidate per horizon
    """
    horizons = horizons if horizons is not None else [7, 30]
    window_configs = window_configs if window_configs is not None else HORIZONS

    rows = load_archive_rows(archive_dir)
    observations = daily_observations(rows)
    coverage = coverage_by_day(rows)

    from app.ingesters.openmeteo import REGION_CAPITALS
    declared_points = sorted(code for code, _lat, _lon in REGION_CAPITALS)

    report: Dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "label": RESULT_LABEL,
        "declaration": DECLARATION,
        "target": TARGET_NAME,
        "manifest_digest": manifest_digest(archive_dir),
        "snapshot_digest": snapshot_digest(rows),
        "git_commit": _git_commit_sha(),
        "period": {
            "start": ARCHIVE_PERIOD_START.isoformat(),
            "end": ARCHIVE_PERIOD_END.isoformat(),
            "days": (ARCHIVE_PERIOD_END - ARCHIVE_PERIOD_START).days + 1,
        },
        "points": {
            "declared": declared_points,
            "count": len(declared_points),
        },
    }

    days_set = sorted({o.period_end for o in observations})
    points_in_data = sorted({o.region for o in observations})

    point_days_below_floor: List[Tuple[str, str]] = []
    for (point, day), hours in coverage.items():
        if hours < required_hours():
            point_days_below_floor.append((point, day.isoformat()))

    missing_points = [p for p in declared_points if p not in points_in_data]

    if days_set:
        expected_days = (days_set[-1] - days_set[0]).days + 1
        has_gaps = len(days_set) != expected_days
    else:
        has_gaps = False

    refused = bool(point_days_below_floor or missing_points or has_gaps)

    if refused:
        report["refused"] = True
        report["refusal_reasons"] = {
            "point_days_below_floor": len(point_days_below_floor),
            "first_few_below_floor": point_days_below_floor[:5],
            "missing_declared_points": missing_points,
            "days_not_contiguous": has_gaps,
        }
        report["day_count"] = len(days_set)
        return report

    report["refused"] = False
    report["day_count"] = len(days_set)

    results_by_horizon: Dict[int, Dict] = {}

    for horizon in horizons:
        config = window_configs.get(horizon)
        if config is None:
            raise ValueError(f"horizon {horizon} not in window configuration")

        n_windows = config["n_windows"]
        n_days = len(days_set)

        train_size = n_days - horizon * n_windows
        if train_size < 1:
            raise ValueError(
                f"h={horizon}: {n_days} days cannot support {n_windows} "
                f"windows (need >= {horizon * n_windows + 1})"
            )

        first_test_idx = train_size
        computed_first_test = days_set[first_test_idx]
        declared_first_test = config["evaluation_period_start"]
        if computed_first_test != declared_first_test:
            raise ValueError(
                f"h={horizon}: computed first test day {computed_first_test.isoformat()} "
                f"!= declared {declared_first_test.isoformat()}"
            )

        baselines = default_baselines(season_length=7, window=7) + [ClimatologyForecast()]

        from .challengers import CANDIDATES
        candidates_to_run = [c for c in CANDIDATES if c.role != "shadow"]

        per_point_reports: Dict[str, Dict[str, EvaluationReport]] = {}
        per_point_failures: Dict[str, Dict[str, str]] = {}

        for point in declared_points:
            point_obs = [o for o in observations if o.region == point]
            frame = pd.DataFrame([
                {"ds": pd.Timestamp(o.period_end), "y": o.value}
                for o in point_obs
            ]).sort_values("ds").reset_index(drop=True)

            if len(frame) < train_size + horizon:
                for model in [b.model_name for b in baselines] + [c.name for c in candidates_to_run]:
                    per_point_failures.setdefault(point, {})[model] = (
                        f"insufficient data: {len(frame)} rows, need {train_size + horizon}"
                    )
                continue

            point_reports: Dict[str, EvaluationReport] = {}
            point_failures: Dict[str, str] = {}

            for baseline in baselines:
                def fit_predict(train_df, h, _model=baseline):
                    _model.fit(train_df, "y")
                    return _model.predict(h).yhat

                try:
                    report_obj = evaluate_rolling_origin(
                        fit_predict, frame, "y",
                        horizon=horizon, train_size=train_size,
                        n_windows=n_windows, season_length=7,
                        model_name=baseline.model_name,
                    )
                    point_reports[baseline.model_name] = report_obj
                except ValueError as exc:
                    point_failures[baseline.model_name] = str(exc)

            for spec in candidates_to_run:
                model = spec.factory()

                def fit_predict_cand(train_df, h, _model=model):
                    _model.fit(train_df, "y")
                    return _model.predict(h).yhat

                try:
                    report_obj = evaluate_rolling_origin(
                        fit_predict_cand, frame, "y",
                        horizon=horizon, train_size=train_size,
                        n_windows=n_windows, season_length=7,
                        model_name=spec.name,
                    )
                    point_reports[spec.name] = report_obj
                except ValueError as exc:
                    point_failures[spec.name] = str(exc)

            per_point_reports[point] = point_reports
            per_point_failures[point] = point_failures

        all_models = {b.model_name for b in baselines} | {c.name for c in candidates_to_run}

        points_scored_per_model: Dict[str, List[str]] = {}
        for model in all_models:
            scored = [
                point for point in declared_points
                if point in per_point_reports and model in per_point_reports[point]
            ]
            points_scored_per_model[model] = scored

        per_point_metrics: Dict[str, Dict[str, Dict]] = {}
        for point in declared_points:
            point_metrics: Dict[str, Dict] = {}
            for model in all_models:
                if point in per_point_reports and model in per_point_reports[point]:
                    rep = per_point_reports[point][model]
                    point_metrics[model] = {
                        "mae": rep.mean("mae"),
                        "rmse": rep.mean("rmse"),
                        "mase": rep.mean("mase"),
                        "bias": rep.mean("bias"),
                    }
            per_point_metrics[point] = point_metrics

        macro_metrics: Dict[str, Dict] = {}
        for model in all_models:
            point_values = [
                per_point_reports[point][model].mean("mae")
                for point in points_scored_per_model[model]
            ]
            if point_values:
                macro_metrics[model] = {
                    "mae": float(np.mean(point_values)),
                    "rmse": float(np.mean([
                        per_point_reports[point][model].mean("rmse")
                        for point in points_scored_per_model[model]
                    ])),
                    "mase": float(np.mean([
                        per_point_reports[point][model].mean("mase")
                        for point in points_scored_per_model[model]
                    ])),
                    "bias": float(np.mean([
                        per_point_reports[point][model].mean("bias")
                        for point in points_scored_per_model[model]
                    ])),
                    "points_scored": len(points_scored_per_model[model]),
                }

        baseline_names = [b.model_name for b in baselines]
        candidate_verdicts: Dict[str, Dict] = {}

        # Check that every declared baseline can be scored for every declared point
        missing_baseline_points: List[Tuple[str, str, str]] = []
        for baseline_name in baseline_names:
            for point in declared_points:
                if point not in points_scored_per_model.get(baseline_name, []):
                    reason = per_point_failures.get(point, {}).get(baseline_name, "unknown")
                    missing_baseline_points.append((baseline_name, point, reason))

        for spec in candidates_to_run:
            cand_name = spec.name

            if cand_name not in macro_metrics:
                candidate_verdicts[cand_name] = {
                    "succeeded": False,
                    "reason": "candidate could not be scored",
                }
                continue

            # If ANY baseline could not be scored for ANY point, verdict is not_comparable
            if missing_baseline_points:
                missing_summary = [f"{bl}/{pt}: {rsn}" for bl, pt, rsn in missing_baseline_points[:5]]
                candidate_verdicts[cand_name] = {
                    "succeeded": False,
                    "reason": "not_comparable",
                    "explanation": "not all declared baselines scored for all declared points",
                    "missing_baseline_point_pairs": len(missing_baseline_points),
                    "missing": missing_summary,
                }
                continue

            baseline_maes = {
                bl: macro_metrics[bl]["mae"]
                for bl in baseline_names
            }

            cand_points = set(points_scored_per_model[cand_name])
            baseline_point_sets = {bl: set(points_scored_per_model[bl]) for bl in baseline_names}
            all_compared_models = [cand_name] + baseline_names
            common_points = cand_points.intersection(*baseline_point_sets.values())

            if len(common_points) < len(declared_points):
                missing_for_any = []
                for point in declared_points:
                    for model in all_compared_models:
                        if point not in points_scored_per_model.get(model, []):
                            reason = per_point_failures.get(point, {}).get(model, "unknown")
                            missing_for_any.append(f"{point}/{model}: {reason}")
                            break

                candidate_verdicts[cand_name] = {
                    "succeeded": False,
                    "reason": "not_comparable",
                    "explanation": "not all declared points scored for all compared models",
                    "missing": missing_for_any[:5],
                    "points_scored": len(common_points),
                    "points_declared": len(declared_points),
                }
                continue

            cand_mae = macro_metrics[cand_name]["mae"]
            beats_all = all(cand_mae < bl_mae for bl_mae in baseline_maes.values())
            strongest_baseline = min(baseline_maes, key=baseline_maes.get)
            strongest_mae = baseline_maes[strongest_baseline]

            window_candidate_maes = []
            window_strongest_maes = []

            for window_idx in range(n_windows):
                window_cand_point_maes = []
                window_bl_point_maes = []

                for point in common_points:
                    cand_rep = per_point_reports[point][cand_name]
                    bl_rep = per_point_reports[point][strongest_baseline]

                    if window_idx < len(cand_rep.windows):
                        window_cand_point_maes.append(cand_rep.windows[window_idx].mae)
                    if window_idx < len(bl_rep.windows):
                        window_bl_point_maes.append(bl_rep.windows[window_idx].mae)

                assert len(window_cand_point_maes) == len(window_bl_point_maes), (
                    f"window {window_idx}: mismatched point counts "
                    f"({len(window_cand_point_maes)} vs {len(window_bl_point_maes)})"
                )

                if window_cand_point_maes:
                    window_candidate_maes.append(np.mean(window_cand_point_maes))
                    window_strongest_maes.append(np.mean(window_bl_point_maes))

            if len(window_candidate_maes) < 2:
                candidate_verdicts[cand_name] = {
                    "succeeded": False,
                    "beats_all": beats_all,
                    "reason": "not enough windows for bootstrap",
                }
                continue

            diffs = np.array(window_candidate_maes) - np.array(window_strongest_maes)
            rng = np.random.default_rng(0)
            bootstrap_means = []
            for _ in range(2000):
                resample = rng.choice(diffs, size=len(diffs), replace=True)
                bootstrap_means.append(resample.mean())

            ci_lower = float(np.percentile(bootstrap_means, 2.5))
            ci_upper = float(np.percentile(bootstrap_means, 97.5))

            succeeded = beats_all and ci_upper < 0

            candidate_verdicts[cand_name] = {
                "succeeded": succeeded,
                "beats_all": beats_all,
                "bootstrap_ci_95": {"lower": ci_lower, "upper": ci_upper},
                "strongest_baseline": strongest_baseline,
                "strongest_baseline_mae": strongest_mae,
                "candidate_mae": cand_mae,
            }

        failure_summary = {}
        for model in all_models:
            failures = []
            for point in declared_points:
                if point in per_point_failures and model in per_point_failures[point]:
                    failures.append(f"{point}: {per_point_failures[point][model]}")
            if failures:
                failure_summary[model] = {"count": len(failures), "first_few": failures[:5]}

        results_by_horizon[horizon] = {
            "n_windows": n_windows,
            "train_size": train_size,
            "first_test_day": computed_first_test.isoformat(),
            "per_point_metrics": per_point_metrics,
            "macro_metrics": macro_metrics,
            "candidate_verdicts": candidate_verdicts,
            "failures": failure_summary,
        }

    report["results_by_horizon"] = results_by_horizon
    return report


def _git_commit_sha() -> Optional[str]:
    """The commit SHA of the code that ran, or None if unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None
