"""Regression suite: pinned scoring outputs for 5 countries x 4 project types."""
import json
from pathlib import Path

import pytest

BASELINE_PATH = Path(__file__).parent / "baseline_scores.json"
TOLERANCE = 2.0

if not BASELINE_PATH.exists():
    pytest.skip("baseline_scores.json missing — run generate_baseline.py first", allow_module_level=True)

CASES = json.loads(BASELINE_PATH.read_text())["cases"]
FLOAT_FIELDS = {"total_score", "environment_score", "social_score", "economic_score", "success_probability", "success_probability_v2"}


@pytest.mark.parametrize("case", CASES, ids=[f"{c['country']}-{c['preset']}" for c in CASES])
def test_score_matches_baseline(client, case):
    r = client.post("/api/v1/evaluate", json=case["payload"])
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
    actual = r.json()
    expected = case["expected"]

    for field, exp_val in expected.items():
        act_val = actual.get(field)
        if field in FLOAT_FIELDS:
            assert act_val is not None, f"missing {field}"
            assert abs(act_val - exp_val) <= TOLERANCE, f"{field}: expected {exp_val}, got {act_val} (delta > {TOLERANCE})"
        else:
            assert act_val == exp_val, f"{field}: expected {exp_val!r}, got {act_val!r}"
