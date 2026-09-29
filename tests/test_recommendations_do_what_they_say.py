"""Test that numeric recommendations reach what they promise.

Finding 14: numeric advice (CO2, social, budget targets) did not achieve the
stated sub-score >= 70. Measured 0 of 54 advices reaching >= 70 on main.

This test verifies:
- every numeric advice, when applied, gives that sub-score >= 70 (>= 0.7 raw)
- minimality: one step less stays < 0.7
- unreachable advices: at max input, sub-score < 0.7 and stated cap matches
- completeness: each sub-score < 70 gets exactly one advice of its dimension
- green-bond sentence: old phrase gone, new text present when condition holds

The grid tests run a comprehensive sweep over all countries, all regions, and
representative input ranges. They use a stub RandomForest (predict_proba returns
[[0.5, 0.5]]) because success_probability is not read by numeric advice, and the
real model would make the grid take minutes rather than seconds.

PERFORMANCE: The grid is computed ONCE in a module-scoped fixture. Each test
reads pre-computed records without calling calculate_esg, so no test comes near
CI's 60s timeout.
"""

import re
import itertools
import collections
import pytest
from app.main import calculate_esg, REGIONAL_FACTORS, _esg_components
from app.schemas import ProjectInput
from app.country_benchmarks import BENCHMARKS, GLOBAL_AVG

# Stub model for the grid tests - numeric advice does not read success_probability
class _StubRandomForest:
    """Stub RandomForest whose predict_proba returns [[0.5, 0.5]].

    Numeric advice (CO2, social, budget targets) is computed entirely from
    sub-scores via _esg_components, which reads only budget/co2/social,
    country benchmarks and regional factors. success_probability feeds only
    the green-bond sentence and the high-risk/excellent recommendations, so
    a fixed 0.5 is sufficient for testing numeric advice over the full grid.

    The real RandomForest makes the grid take minutes; the stub brings it to
    seconds.
    """
    def predict_proba(self, X):
        return [[0.5, 0.5]]


@pytest.fixture(scope="module")
def stub_model_for_grid():
    """Monkeypatch rf_model, make_features and ensemble_model_v2 for grid tests only.

    The patch is module-scoped so it applies once and is restored afterwards.
    The real model is needed for the green-bond tests (which check
    success_probability) and the HTTP smoke test.
    """
    import app.main

    original_rf = app.main.rf_model
    original_make_features = app.main.make_features
    original_ensemble = app.main.ensemble_model_v2

    app.main.rf_model = _StubRandomForest()
    app.main.make_features = lambda p: None  # Not called when rf_model is stub
    app.main.ensemble_model_v2 = None  # Not used for numeric advice

    yield {
        "original_rf": original_rf,
        "original_make_features": original_make_features,
        "original_ensemble": original_ensemble,
    }

    app.main.rf_model = original_rf
    app.main.make_features = original_make_features
    app.main.ensemble_model_v2 = original_ensemble


@pytest.fixture
def real_model(stub_model_for_grid):
    """Temporarily restore the real model for tests that need success_probability.

    This fixture depends on stub_model_for_grid to ensure it's set up first,
    then temporarily restores the real model for the duration of the test.
    """
    import app.main

    originals = stub_model_for_grid

    # Temporarily restore real model
    app.main.rf_model = originals["original_rf"]
    app.main.make_features = originals["original_make_features"]
    app.main.ensemble_model_v2 = originals["original_ensemble"]

    yield

    # Restore stub
    app.main.rf_model = _StubRandomForest()
    app.main.make_features = lambda p: None
    app.main.ensemble_model_v2 = None


# Grid for comprehensive testing
ALL_COUNTRIES = sorted(BENCHMARKS.keys()) + ["Not_In_Benchmarks"]
ALL_REGIONS = list(REGIONAL_FACTORS.keys())
BUDGETS = [5_000, 30_000, 150_000, 2_000_000]
CO2S = [0, 60, 300, 499, 650]
SOCIALS = [0, 3, 6, 9, 10]
DURATION = 24

# Patterns for advice extraction
PATTERNS = {
    "env_num": re.compile(r"^Increase CO2 reduction from ([\d.]+) to (\d+)\+ t/yr to reach a Strong environmental rating$"),
    "soc_num": re.compile(r"^Raise social impact from ([\d.]+) to (\d+)\+ to reach a Strong social rating"),
    "eco_num": re.compile(r"^Increase budget from \$([\d,]+) to \$([\d,]+)\+ to reach a Strong economic rating$"),
    "env_out": re.compile(r"^A Strong environmental rating \(70\) is out of reach in .*: at 500\+ t/yr the score is ([\d.]+);"),
    "soc_out": re.compile(r"^A Strong social rating \(70\) is out of reach in .*: at the maximum social impact of 10 the score is ([\d.]+),"),
    "eco_out": re.compile(r"^A Strong economic rating \(70\) is out of reach in .*: at any budget the score is capped at ([\d.]+),"),
}


@pytest.fixture(scope="module")
def grid_records(stub_model_for_grid):
    """Compute the grid ONCE and return pre-computed records for all property tests.

    Each record contains:
    - inputs: (country, region, budget, co2, social)
    - raw_scores: (env_raw, soc_raw, eco_raw)
    - recommendations: list of strings
    - advice: list of (kind, parsed_data) tuples where parsed_data has:
        - target: int (for numeric advice)
        - stated_cap: float (for out-of-reach advice)
        - applied_score: score at target (for numeric)
        - one_less_score: score at target-1 (for minimality)
        - max_score: score at max input (for out-of-reach)
    """
    records = []

    for country, region, budget, co2, social in itertools.product(
        ALL_COUNTRIES, ALL_REGIONS, BUDGETS, CO2S, SOCIALS
    ):
        project = ProjectInput(
            budget=budget,
            co2_reduction=co2,
            social_impact=social,
            duration_months=DURATION,
            category="Solar Energy"
        )
        project.region = country
        project.name = "test"

        result = calculate_esg(project, region)

        # Get raw sub-scores
        country_benchmark = BENCHMARKS.get(country, GLOBAL_AVG)
        regional_factors = REGIONAL_FACTORS[region]
        env_raw, soc_raw, eco_raw = _esg_components(budget, co2, social, country_benchmark, regional_factors)

        # Parse advice from recommendations
        advice = []
        for rec in result["recommendations"]:
            for kind, pattern in PATTERNS.items():
                m = pattern.match(rec)
                if m:
                    parsed = {"kind": kind}

                    if kind == "env_num":
                        target = int(m.group(2))
                        env_at_target, _, _ = _esg_components(budget, target, social, country_benchmark, regional_factors)
                        env_at_less, _, _ = _esg_components(budget, target - 1, social, country_benchmark, regional_factors) if target - 1 > co2 else (None, None, None)
                        parsed.update({
                            "target": target,
                            "applied_score": env_at_target,
                            "one_less_score": env_at_less,
                        })

                    elif kind == "soc_num":
                        target = int(m.group(2))
                        _, soc_at_target, _ = _esg_components(budget, co2, target, country_benchmark, regional_factors)
                        _, soc_at_less, _ = _esg_components(budget, co2, target - 1, country_benchmark, regional_factors) if target - 1 > social else (None, None, None)
                        parsed.update({
                            "target": target,
                            "applied_score": soc_at_target,
                            "one_less_score": soc_at_less,
                        })

                    elif kind == "eco_num":
                        target = float(m.group(2).replace(",", ""))
                        _, _, eco_at_target = _esg_components(target, co2, social, country_benchmark, regional_factors)
                        _, _, eco_at_less = _esg_components(target - 1000, co2, social, country_benchmark, regional_factors) if target - 1000 > budget else (None, None, None)
                        parsed.update({
                            "target": target,
                            "applied_score": eco_at_target,
                            "one_less_score": eco_at_less,
                        })

                    elif kind == "env_out":
                        stated_cap = float(m.group(1))
                        env_at_max, _, _ = _esg_components(budget, 500, social, country_benchmark, regional_factors)
                        parsed.update({
                            "stated_cap": stated_cap,
                            "max_score": env_at_max,
                        })

                    elif kind == "soc_out":
                        stated_cap = float(m.group(1))
                        _, soc_at_max, _ = _esg_components(budget, co2, 10, country_benchmark, regional_factors)
                        parsed.update({
                            "stated_cap": stated_cap,
                            "max_score": soc_at_max,
                        })

                    elif kind == "eco_out":
                        stated_cap = float(m.group(1))
                        _, _, eco_at_max = _esg_components(1e12, co2, social, country_benchmark, regional_factors)
                        parsed.update({
                            "stated_cap": stated_cap,
                            "max_score": eco_at_max,
                        })

                    advice.append(parsed)
                    break

        records.append({
            "inputs": (country, region, budget, co2, social),
            "raw_scores": (env_raw, soc_raw, eco_raw),
            "recommendations": result["recommendations"],
            "advice": advice,
        })

    return records


def test_completeness_every_dimension_below_70_gets_exactly_one_advice(grid_records):
    """Completeness: each sub-score < 0.7 gets exactly one advice of its dimension, >= 0.7 none."""
    violations = []

    for rec in grid_records:
        country, region, budget, co2, social = rec["inputs"]
        env_raw, soc_raw, eco_raw = rec["raw_scores"]

        # Count advice by dimension
        env_count = sum(1 for a in rec["advice"] if a["kind"] in ("env_num", "env_out"))
        soc_count = sum(1 for a in rec["advice"] if a["kind"] in ("soc_num", "soc_out"))
        eco_count = sum(1 for a in rec["advice"] if a["kind"] in ("eco_num", "eco_out"))

        for dim, raw_score, count in [("env", env_raw, env_count), ("soc", soc_raw, soc_count), ("eco", eco_raw, eco_count)]:
            expected = 1 if raw_score < 0.7 else 0
            if count != expected:
                violations.append((
                    "completeness", dim, country, region, budget, co2, social,
                    f"got {count} advice, expected {expected} (raw={raw_score:.3f})"
                ))

    assert len(violations) == 0, f"Completeness violations: {violations[:10]}"


def test_numeric_advice_reaches_70_when_applied(grid_records):
    """Every numeric advice, applied, gives that sub-score >= 0.7."""
    violations = []

    for rec in grid_records:
        country, region, budget, co2, social = rec["inputs"]

        for advice in rec["advice"]:
            if advice["kind"] in ("env_num", "soc_num", "eco_num"):
                if advice["applied_score"] < 0.7:
                    violations.append((
                        "does not reach", advice["kind"], country, region, budget, co2, social,
                        f"target={advice['target']}, got score={advice['applied_score']:.3f}"
                    ))

    assert len(violations) == 0, f"Reachability violations: {violations[:10]}"


def test_minimality_one_step_less_stays_below_70(grid_records):
    """Minimality: target-1 (or target-1000 for budget) stays < 0.7 when > current."""
    violations = []

    for rec in grid_records:
        country, region, budget, co2, social = rec["inputs"]

        for advice in rec["advice"]:
            if advice["kind"] in ("env_num", "soc_num", "eco_num"):
                if advice["one_less_score"] is not None and advice["one_less_score"] >= 0.7:
                    violations.append((
                        "not minimal", advice["kind"], country, region, budget, co2, social,
                        f"target={advice['target']}, one_less gives score={advice['one_less_score']:.3f}"
                    ))

    assert len(violations) == 0, f"Minimality violations: {violations[:10]}"


def test_out_of_reach_advice_correct_cap_and_unreachable(grid_records):
    """Out-of-reach advice: at max input, sub-score < 0.7 and stated cap equals round(max*100, 1)."""
    violations = []

    for rec in grid_records:
        country, region, budget, co2, social = rec["inputs"]

        for advice in rec["advice"]:
            if advice["kind"] in ("env_out", "soc_out", "eco_out"):
                max_score = advice["max_score"]
                stated_cap = advice["stated_cap"]

                # Check that max score < 0.7
                if max_score >= 0.7:
                    violations.append((
                        "claims out of reach but reachable", advice["kind"], country, region, budget, co2, social,
                        f"max_score={max_score:.3f} >= 0.7"
                    ))

                # Check cap matches
                expected_cap = round(max_score * 100, 1)
                if abs(stated_cap - expected_cap) > 0.05:
                    violations.append((
                        "wrong cap", advice["kind"], country, region, budget, co2, social,
                        f"stated={stated_cap}, expected={expected_cap} (raw={max_score:.3f})"
                    ))

    assert len(violations) == 0, f"Out-of-reach violations: {violations[:10]}"


def test_non_vacuity_each_advice_kind_occurs_at_least_100_times(grid_records):
    """Each of the six advice kinds must occur at least 100 times over the grid."""
    counts = collections.Counter()

    for rec in grid_records:
        for advice in rec["advice"]:
            counts[advice["kind"]] += 1

    failures = []
    for kind in PATTERNS:
        if counts[kind] < 100:
            failures.append(f"{kind}={counts[kind]}")

    assert len(failures) == 0, f"Non-vacuity failures: {failures}. All counts: {dict(counts)}"


def test_green_bond_sentence_new_text(real_model):
    """Green-bond sentence: old phrase gone, new text present when condition holds.

    Uses the REAL model since this checks success_probability.
    """
    # Build a project meeting the green-bond condition: total >= 75 and success_prob >= 70
    project = ProjectInput(
        budget=200_000,
        co2_reduction=300,
        social_impact=9,
        duration_months=24,
        category="Solar Energy"
    )
    project.region = "Germany"
    project.name = "test"

    result = calculate_esg(project, "Europe")

    # Assert condition holds first
    assert result["total_score"] >= 75, \
        f"Expected total_score >= 75, got {result['total_score']}"
    assert result["success_probability"] >= 70, \
        f"Expected success_probability >= 70, got {result['success_probability']}"

    # New text should be present
    assert any("[OK] Strong ESG profile: a candidate for assessment against green bond standards" in r
               for r in result["recommendations"]), \
        f"Green bond condition met but new text not found in {result['recommendations']}"
    # Old text should not be present
    assert not any("green bond certification" in r for r in result["recommendations"]), \
        "Old green bond text still present"


def test_old_green_bond_phrase_absent_in_app():
    """The old green bond phrase should not appear anywhere under app/."""
    from pathlib import Path

    # Find app directory relative to this test file
    app_dir = Path(__file__).resolve().parents[1] / "app"

    # Walk all .py files under app/
    offending_files = []
    for py_file in app_dir.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
            if "green bond certification" in content:
                offending_files.append(str(py_file.relative_to(app_dir.parent)))
        except Exception:
            pass  # Skip files we can't read

    assert len(offending_files) == 0, \
        f"Old green bond text 'green bond certification' found in: {offending_files}"


def test_http_smoke_evaluate_returns_new_advice(client, real_model):
    """HTTP smoke: POST /api/v1/evaluate returns new-style advice.

    Uses the REAL model through the HTTP layer.
    """
    response = client.post("/api/v1/evaluate", json={
        "budget": 50000,
        "co2_reduction": 50,
        "social_impact": 5,
        "duration_months": 24,
        "category": "Solar Energy",
        "region": "Germany",
        "name": "test"
    })
    assert response.status_code == 200
    data = response.json()
    assert "recommendations" in data
    # At least one of the new-style advice texts should be present or an "out of reach" message
    has_new_style = any(
        "to reach a Strong environmental rating" in r or
        "to reach a Strong social rating" in r or
        "to reach a Strong economic rating" in r or
        "out of reach" in r
        for r in data["recommendations"]
    )
    assert has_new_style, f"No new-style advice in {data['recommendations']}"
