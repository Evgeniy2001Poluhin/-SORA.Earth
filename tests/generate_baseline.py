"""Generate baseline regression scores by calling the live API.
7 countries x 6 presets = 42 pinned cases.
"""
import json
import sys
from pathlib import Path

import httpx

API = "http://localhost:8000/api/v1/evaluate"

COUNTRIES = ["Sweden", "Germany", "UK", "USA", "Brazil", "India", "Japan"]

PRESETS = {
    "Solar":         {"co2_reduction_tons_per_year": 250, "budget_usd": 150000, "social_impact_score": 8, "project_duration_months": 24},
    "Wind":          {"co2_reduction_tons_per_year": 400, "budget_usd": 250000, "social_impact_score": 7, "project_duration_months": 30},
    "Hydro":         {"co2_reduction_tons_per_year": 600, "budget_usd": 400000, "social_impact_score": 6, "project_duration_months": 48},
    "Reforestation": {"co2_reduction_tons_per_year": 500, "budget_usd": 80000,  "social_impact_score": 9, "project_duration_months": 36},
    "EV_charging":   {"co2_reduction_tons_per_year": 180, "budget_usd": 200000, "social_impact_score": 7, "project_duration_months": 18},
    "Water":         {"co2_reduction_tons_per_year": 50,  "budget_usd": 60000,  "social_impact_score": 9, "project_duration_months": 12},
}

ASSERT_FIELDS = [
    "total_score", "environment_score", "social_score", "economic_score",
    "success_probability", "success_probability_v2", "risk_level"
]


def main():
    out, skipped = [], []
    with httpx.Client(timeout=10.0) as client:
        for country in COUNTRIES:
            for preset_name, preset in PRESETS.items():
                payload = {"project_name": preset_name, "country": country, **preset}
                try:
                    r = client.post(API, json=payload)
                    if r.status_code != 200:
                        print("SKIP", country, preset_name, "HTTP", r.status_code)
                        skipped.append({"country": country, "preset": preset_name, "status": r.status_code})
                        continue
                    body = r.json()
                    expected = {k: body[k] for k in ASSERT_FIELDS if k in body}
                    out.append({"country": country, "preset": preset_name, "payload": payload, "expected": expected})
                    score = body.get("total_score")
                    risk = body.get("risk_level")
                    print("OK  ", country, "/", preset_name, "score=", score, "risk=", risk)
                except Exception as e:
                    print("ERR ", country, preset_name, e)
                    skipped.append({"country": country, "preset": preset_name, "error": str(e)})

    target = Path("tests/baseline_scores.json")
    target.write_text(json.dumps({"cases": out, "skipped": skipped}, indent=2))
    print("Written", len(out), "cases to", target, "(skipped", len(skipped), ")")
    if not out:
        sys.exit(1)


if __name__ == "__main__":
    main()
