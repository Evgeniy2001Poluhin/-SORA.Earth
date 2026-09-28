"""Tests for forecast report target declaration and verdict placement (finding 35).

Two defects measured on the 2026-09-27 archive backtest:

1. comparison_summary is computed on points_in_data[0] only (the alphabetically
   first point) and placed at candidate level beside the all-region verdict, so
   a reader takes one point's "beats_all" as the candidate's verdict.

2. report["evidential"] is set from refusals (gate, baselines, point set) but
   never checks that the rows are the declared target; the declared M3 target
   exists only as DEFAULT_SOURCE/DEFAULT_INDICATOR in scripts/evaluate_forecast.py.

The fix:
(a) Remove single-point compare_to_baselines call and comparison_summary field
    (all-region fields beats_every_baseline, per_baseline_mae_ratios stay)
(b) Declare target once in entry_conditions.py (DECLARED_SOURCE, DECLARED_INDICATOR,
    DECLARED_TARGET); scripts/evaluate_forecast.py takes defaults from there
(c) build_report adds a refusal when target != DECLARED_TARGET, making evidential
    False, naming both, recording report["declared_target"]
"""
from datetime import datetime, timedelta, timezone
import math
import random
import json

import pytest
import numpy as np

from app.services.forecasting.report import build_report


def _series_with_pattern(
    points=("region_00",),
    days=200,
    start=datetime(2026, 1, 1, tzinfo=timezone.utc),
    pattern_fn=None,
):
    """Generate a synthetic hourly series with a given pattern function.

    Args:
        points: Region names
        days: Number of days
        start: Start datetime
        pattern_fn: Function (region_idx, day_idx) -> daily_value
                    If None, uses weekly seasonality + drift
    """
    rng = random.Random(42)
    rows = []

    for r_idx, point in enumerate(points):
        level = 10.0
        for d in range(days):
            if pattern_fn is not None:
                daily_value = pattern_fn(r_idx, d)
            else:
                # Default: weekly seasonality + drift (to avoid zero MASE denominator)
                level += rng.uniform(-0.5, 0.5)
                daily_value = level + 5 * math.sin(d / 7 * 2 * math.pi)

            for hour in range(24):
                value = daily_value + hour / 24  # Small hourly variation
                rows.append((point, start + timedelta(days=d, hours=hour), value))

    return rows


def _learnable_only_in_first_point(points=("region_00", "region_01", "region_02"), days=200):
    """Generate data where only the first (alphabetically) point has a learnable pattern.

    First point: clean linear trend that linear_trend can fit perfectly
    Other points: noise and seasonality that makes linear_trend lose

    This triggers defect 1: if comparison_summary is computed only on the first point,
    it will show "beats_all" while beats_every_baseline is False.
    """
    def pattern(region_idx, day_idx):
        if region_idx == 0:
            # Clean linear trend: linear_trend model will fit this perfectly
            return 10.0 + 0.5 * day_idx
        else:
            # Noise + strong seasonality: linear_trend will lose here
            np.random.seed(region_idx * 1000 + day_idx)
            seasonal = 10.0 * math.sin(2 * math.pi * day_idx / 7)
            noise = np.random.randn() * 3.0
            return 20.0 + seasonal + noise

    return _series_with_pattern(points=points, days=days, pattern_fn=pattern)


def test_no_single_point_verdict_in_candidate_section():
    """T2: First point alone gives 'beats_all' but across all points candidate loses.

    This test MUST fail on main because comparison_summary.verdict == "beats_all"
    from the first point only.
    """
    rows = _learnable_only_in_first_point(
        points=("region_00", "region_01", "region_02"),
        days=200
    )
    declared = sorted({r[0] for r in rows})

    report = build_report(
        rows,
        target="synthetic:mixed",
        horizons=[7],
        declared_points=declared,
        season_length=7,
        candidates=["linear_trend"],
    )

    # Gate should pass
    assert report["gate"]["7"]["passed"], "Gate should pass with sufficient data"

    # Check the candidate was evaluated
    linear = report["candidates"].get("linear_trend", {})
    assert linear.get("evaluated"), "linear_trend should be evaluated"

    # The candidate should NOT beat every baseline across all regions
    # (because it fails on regions 1 and 2)
    assert linear["beats_every_baseline"] is False, (
        "linear_trend should NOT beat every baseline across all regions"
    )

    # CRITICAL: comparison_summary should NOT be present
    assert "comparison_summary" not in linear, (
        "comparison_summary should not be present in the candidate section"
    )

    # The string "beats_all" should not appear anywhere in the candidate's section
    def walk_for_beats_all(obj, path=""):
        """Recursively search for 'beats_all' string in the candidate section."""
        if isinstance(obj, dict):
            for key, value in obj.items():
                if walk_for_beats_all(value, f"{path}.{key}"):
                    return True
        elif isinstance(obj, (list, tuple)):
            for i, item in enumerate(obj):
                if walk_for_beats_all(item, f"{path}[{i}]"):
                    return True
        elif isinstance(obj, str):
            if "beats_all" in obj:
                return True
        return False

    assert not walk_for_beats_all(linear), (
        "'beats_all' verdict appears in linear_trend section but beats_every_baseline is False"
    )

    # All-region fields should still be present
    assert "mean_mae" in linear
    assert "worst_region_mae" in linear
    assert "per_baseline_mae_ratios" in linear


def test_evidential_only_for_declared_target():
    """T1: Report is evidential only when target matches DECLARED_TARGET.

    Control: target=DECLARED_TARGET with passing rows -> evidential True
    Treatment: target="openmeteo-archive:temperature_2m" -> evidential False
    """
    from app.services.forecasting.entry_conditions import (
        DECLARED_TARGET,
        DECLARED_SOURCE,
        DECLARED_INDICATOR,
    )

    # Generate passing data: 3 regions, 260 days (enough for h=7)
    rows = _series_with_pattern(
        points=("region_00", "region_01", "region_02"),
        days=260,
    )
    declared = sorted({r[0] for r in rows})

    # Control: declared target
    report_declared = build_report(
        rows,
        target=DECLARED_TARGET,
        horizons=[7],
        declared_points=declared,
        season_length=7,
    )

    # Gate should pass
    assert report_declared["gate"]["7"]["passed"], (
        "Synthetic data should pass the section 7 gate"
    )

    # At least one baseline should be scored
    assert any(b["scored"] for b in report_declared["baselines"].values()), (
        "At least one baseline should be scored on this data"
    )

    # Control: evidential should be True for declared target
    assert report_declared["evidential"] is True, (
        f"Report with target={DECLARED_TARGET} and passing conditions should be evidential"
    )

    # declared_target should be recorded
    assert report_declared.get("declared_target") == DECLARED_TARGET, (
        "Report should record declared_target"
    )

    # Treatment: non-declared target (e.g., archive backtest)
    report_archive = build_report(
        rows,
        target="openmeteo-archive:temperature_2m",
        horizons=[7],
        declared_points=declared,
        season_length=7,
    )

    # Gate should still pass (same data)
    assert report_archive["gate"]["7"]["passed"]
    assert any(b["scored"] for b in report_archive["baselines"].values())

    # Treatment: evidential should be False for non-declared target
    assert report_archive["evidential"] is False, (
        "Report with target != DECLARED_TARGET should not be evidential"
    )

    # why_not_evidential should name both targets
    refusals = " ".join(report_archive["why_not_evidential"])
    assert "openmeteo-archive:temperature_2m" in refusals, (
        "Refusal should name the given target"
    )
    assert DECLARED_TARGET in refusals or (DECLARED_SOURCE in refusals and DECLARED_INDICATOR in refusals), (
        "Refusal should name the declared target"
    )


def test_challengers_test_updated_for_new_shape():
    """Update test_forecast_challengers.py line 147 check for new shape.

    This verifies the all-region fields are present without comparison_summary.
    """
    rows = _series_with_pattern(
        points=("region_00", "region_01", "region_02"),
        days=260
    )
    declared = sorted({r[0] for r in rows})

    report = build_report(
        rows,
        target="synthetic:test",
        horizons=[7],
        declared_points=declared,
        season_length=7,
    )

    candidates_section = report["candidates"]
    for name, info in candidates_section.items():
        if info.get("evaluated"):
            # All-region fields should be present
            assert "mean_mase" in info, f"{name}: should have mean_mase"
            assert "mean_mae" in info, f"{name}: should have mean_mae"
            assert "worst_region" in info, f"{name}: should have worst_region"
            assert "beats_every_baseline" in info, f"{name}: should have beats_every_baseline"
            assert "per_baseline_mae_ratios" in info, f"{name}: should have per_baseline_mae_ratios"

            # comparison_summary should NOT be present
            assert "comparison_summary" not in info, (
                f"{name}: comparison_summary should not be present"
            )


def test_evaluate_forecast_script_defaults_to_declared_constants():
    """The script defaults equal DECLARED_SOURCE/DECLARED_INDICATOR."""
    import importlib.util
    import os

    from app.services.forecasting.entry_conditions import (
        DECLARED_SOURCE,
        DECLARED_INDICATOR,
    )

    REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    SCRIPT = os.path.join(REPO_ROOT, "scripts", "evaluate_forecast.py")

    spec = importlib.util.spec_from_file_location("evaluate_forecast", SCRIPT)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    assert script.DEFAULT_SOURCE == DECLARED_SOURCE, (
        f"Script DEFAULT_SOURCE should equal DECLARED_SOURCE from entry_conditions"
    )
    assert script.DEFAULT_INDICATOR == DECLARED_INDICATOR, (
        f"Script DEFAULT_INDICATOR should equal DECLARED_INDICATOR from entry_conditions"
    )


if __name__ == "__main__":
    import time

    print("Running forecast report target and verdict tests...")
    start = time.time()

    test_no_single_point_verdict_in_candidate_section()
    print("✓ T2: No single-point verdict in candidate section")

    test_evidential_only_for_declared_target()
    print("✓ T1: Evidential only for declared target")

    test_challengers_test_updated_for_new_shape()
    print("✓ Updated: All-region fields present without comparison_summary")

    test_evaluate_forecast_script_defaults_to_declared_constants()
    print("✓ Script: Defaults match declared constants")

    elapsed = time.time() - start
    print(f"\nAll tests passed in {elapsed:.1f}s")
