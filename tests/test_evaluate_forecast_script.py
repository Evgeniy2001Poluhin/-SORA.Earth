"""The report has a caller, and the caller passes the declared point set.

A harness nobody runs is the defect this whole line of work keeps finding:
`evaluation.py` was imported by tests and by nothing else, and the phase it
belongs to read as done. Adding `report.py` without a caller would repeat that
exactly one level up, so the script is part of the same change and this pins it.

The substantive half is the point set. §7 asks whether every declared region is
present; if the "declared" set is whatever the query returned, the condition is
vacuously true and the report passes a test that was never run. The script
passes the set from the declaration, and the report records which of the two
happened.
"""
import importlib.util
import json
import os
import random
from datetime import datetime, timedelta, timezone

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "evaluate_forecast.py")


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("evaluate_forecast", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_script_exists_and_imports(script):
    assert hasattr(script, "main")
    assert hasattr(script, "load_rows")


def test_the_declared_points_come_from_the_ingester_not_a_copy(script):
    """§2 of the declaration names REGION_CAPITALS as the set itself.

    A second list kept in the script is how two populations come to be compared
    under one name -- the thing §1.4 of the M2 protocol forbids -- and it would
    drift silently the first time a point was added.
    """
    from app.ingesters.openmeteo import REGION_CAPITALS

    points = script.declared_points()

    assert points == sorted(code for code, _lat, _lon in REGION_CAPITALS)
    assert len(points) == 21, (
        f"the declaration fixes twenty-one points; the ingester now visits "
        f"{len(points)}. Adding or removing one is a version bump of "
        f"openmeteo-points-v1, not an edit."
    )


def test_it_reports_a_refusal_rather_than_failing_when_there_is_no_history(
        script, monkeypatch, capsys):
    """Today's real output, and the exit code that goes with it.

    The gate refuses until 2027-02-24 and that is the report's content, not an
    error: a scheduled run must not page anyone for the gate saying what it is
    supposed to say.
    """
    monkeypatch.setattr(script, "load_rows", lambda *a, **k: [])

    code = script.main([])

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["evidential"] is False
    assert report["why_not_evidential"]
    assert report["snapshot"]["declared_points"] == 21
    assert report["snapshot"]["declared_points_source"] == "caller", (
        "the script let the report take the point set from the data, which "
        "makes section 7's 'every declared region present' vacuous"
    )


def test_a_real_snapshot_flows_through_to_scored_baselines(
        script, monkeypatch, capsys):
    """End to end on the shape the query returns, so the wiring is exercised
    rather than asserted."""
    # Drifting, not exactly periodic. `10 + (day % 7)` repeats every seven days
    # exactly, which makes MASE's denominator zero -- the harness then refuses
    # every baseline with "seasonal scale is zero: the training slice is
    # perfectly periodic", correctly, and the test measures nothing. The first
    # version of this test did that and reported "a 200-day series scored no
    # baseline", which was true of the data rather than of the code.
    rng = random.Random(3)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for point in script.declared_points()[:2]:
        level = 10.0
        for day in range(200):
            level += rng.uniform(-0.5, 0.5)
            for hour in range(24):
                rows.append((
                    point,
                    start + timedelta(days=day, hours=hour),
                    level + (day % 7) + hour / 24,
                ))

    monkeypatch.setattr(script, "load_rows", lambda *a, **k: rows)

    assert script.main([]) == 0
    report = json.loads(capsys.readouterr().out)

    scored = report["baselines"][script.declared_points()[0]]["scored"]
    assert scored, "a 200-day series scored no baseline"
    assert report["baselines"][script.declared_points()[0]]["hardest"] in scored
