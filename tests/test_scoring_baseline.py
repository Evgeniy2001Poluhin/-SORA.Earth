"""Regression suite: pinned scoring outputs, read from tests/baseline_scores.json.

42 cases -- seven countries by six project presets. The line here said "5
countries x 4 project types", which is 20; the file has held 42 since it was
generated, and the number was never checked because this suite ran nowhere.
`backend-tests` excluded it with `--ignore` and no other job named it, from the
initial commit of 2026-05-07 until it was wired in on 2026-09-07.

The count is asserted below rather than restated here, so this docstring cannot
go stale again on its own.
"""
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


def test_the_baseline_covers_the_grid_it_claims():
    """The count, from the data rather than from a sentence about it.

    Its docstring said "5 countries x 4 project types" while the file held
    seven by six. Nobody noticed, because the suite was excluded from CI and
    the number had no reader.
    """
    # Emptiness first, and here rather than at module level. A parametrized
    # suite over an empty list is zero tests and a green run -- but a module
    # level `assert` fails at *collection*, which prints no summary line at
    # all, and a harness reading that line cannot tell it from success. The
    # same trap cost a whole mutation run its verdict on 2026-09-06.
    assert CASES, f"{BASELINE_PATH} has no cases; this file would run nothing"

    countries = {c["country"] for c in CASES}
    presets = {c["preset"] for c in CASES}

    assert len(CASES) == len(countries) * len(presets), (
        f"{len(CASES)} cases but {len(countries)} countries x {len(presets)} "
        f"presets = {len(countries) * len(presets)}; the grid has holes or "
        f"duplicates"
    )
    assert len(CASES) >= 40, f"only {len(CASES)} pinned cases"
