"""
Regression tests for SORA.Earth scoring engine.

Tests every (country, preset) combination against tests/baseline_scores.json.
A score change >2% in any cell = test failure.

To regenerate baseline (after intentional formula change):
    python tests/build_baseline.py

To run:
    pytest tests/test_scoring_baseline.py -v
"""
import json
import pytest
import httpx
from pathlib import Path

BASELINE_PATH = Path(__file__).parent / "baseline_scores.json"
URL = "http://localhost:8000/api/v1/evaluate"
TOLERANCE = 2.0  # absolute points; total_score scale is 0-100

PRESETS = {
    "solar":         {"budget": 150000, "co2_reduction": 340, "social_impact": 9, "duration_months": 18, "name": "Solar"},
    "wind":          {"budget": 280000, "co2_reduction": 620, "social_impact": 8, "duration_months": 24, "name": "Wind"},
    "reforestation": {"budget": 90000,  "co2_reduction": 280, "social_impact": 7, "duration_months": 36, "name": "Reforestation"},
    "water":         {"budget": 120000, "co2_reduction": 180, "social_impact": 9, "duration_months": 20, "name": "Water"},
    "ev":            {"budget": 350000, "co2_reduction": 780, "social_impact": 7, "duration_months": 28, "name": "EV"},
    "waste":         {"budget": 100000, "co2_reduction": 240, "social_impact": 8, "duration_months": 16, "name": "Waste"},
}


def _load_baseline():
    if not BASELINE_PATH.exists():
        pytest.skip(f"baseline not found: {BASELINE_PATH}; run build_baseline.py first")
    return json.loads(BASELINE_PATH.read_text())


def _baseline_keys():
    if not BASELINE_PATH.exists():
        return []
    return sorted(json.loads(BASELINE_PATH.read_text()).keys())


@pytest.mark.parametrize("case_key", _baseline_keys())
def test_scoring_matches_baseline(case_key):
    baseline = _load_baseline()
    expected = baseline[case_key]
    country, preset_name = case_key.split("_", 1)
    preset = PRESETS[preset_name]
    payload = {**preset, "region": country}

    try:
        actual = httpx.post(URL, json=payload, timeout=30).json()
    except httpx.HTTPError as e:
        pytest.skip(f"backend not reachable: {e}")

    for field in ("total_score", "environment_score", "social_score", "economic_score"):
        diff = abs(actual[field] - expected[field])
        assert diff <= TOLERANCE, (
            f"{case_key}.{field}: expected={expected[field]:.2f} "
            f"actual={actual[field]:.2f} diff={diff:.2f} > {TOLERANCE}"
        )

    assert actual["risk_level"] == expected["risk_level"], (
        f"{case_key}.risk_level: expected={expected['risk_level']} actual={actual['risk_level']}"
    )
