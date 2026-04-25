"""Regression: scoring stability across 7 countries × 6 presets.
Tolerance ±2.0 absolute. Diploma §5.3."""
import json, httpx, pytest
from pathlib import Path

CASES = json.loads(Path(__file__).parent.joinpath("baseline_scores.json").read_text())

PRESETS = {
    "solar":         {"project_name":"Solar Farm","budget_usd":150000,"co2_reduction_tons_per_year":340,"social_impact_score":9,"project_duration_months":18},
    "wind":          {"project_name":"Offshore Wind","budget_usd":280000,"co2_reduction_tons_per_year":620,"social_impact_score":8,"project_duration_months":24},
    "reforestation": {"project_name":"Boreal Reforestation","budget_usd":80000,"co2_reduction_tons_per_year":280,"social_impact_score":7,"project_duration_months":36},
    "water":         {"project_name":"Urban Water Grid","budget_usd":120000,"co2_reduction_tons_per_year":180,"social_impact_score":9,"project_duration_months":20},
    "ev":            {"project_name":"Fast-Charge Network","budget_usd":450000,"co2_reduction_tons_per_year":780,"social_impact_score":7,"project_duration_months":28},
    "waste":         {"project_name":"Circular Materials","budget_usd":95000,"co2_reduction_tons_per_year":240,"social_impact_score":8,"project_duration_months":16},
}
TOL = 2.0

@pytest.mark.parametrize("key", list(CASES.keys()))
def test_scoring_baseline(key):
    country, preset = key.rsplit("_", 1)
    body = {**PRESETS[preset], "country":country, "region":country}
    r = httpx.post("http://localhost:8000/api/v1/evaluate", json=body, timeout=10)
    r.raise_for_status()
    d = r.json(); exp = CASES[key]
    for k in ("total_score","environment_score","social_score","economic_score"):
        assert abs(d[k] - exp[k]) < TOL, f"{key} {k}: live={d[k]} baseline={exp[k]}"
    assert d["risk_level"].lower() == exp["risk_level"].lower(), f"{key} risk: {d['risk_level']} vs {exp['risk_level']}"
