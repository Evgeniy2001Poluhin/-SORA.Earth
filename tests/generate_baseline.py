"""One-shot: dump current /api/v1/evaluate responses as baseline.
Run again only when scoring formulas legitimately change."""
import json, httpx, itertools, sys
from pathlib import Path

COUNTRIES = ["Sweden","Norway","Germany","USA","Brazil","India","Kenya"]
PRESETS = [
    {"id":"solar", "project_name":"Solar Farm",          "budget_usd":150000, "co2_reduction_tons_per_year":340, "social_impact_score":9, "project_duration_months":18},
    {"id":"wind",  "project_name":"Offshore Wind",       "budget_usd":280000, "co2_reduction_tons_per_year":620, "social_impact_score":8, "project_duration_months":24},
    {"id":"refo",  "project_name":"Boreal Reforestation","budget_usd":80000,  "co2_reduction_tons_per_year":280, "social_impact_score":7, "project_duration_months":36},
    {"id":"water", "project_name":"Urban Water Grid",    "budget_usd":120000, "co2_reduction_tons_per_year":180, "social_impact_score":9, "project_duration_months":20},
    {"id":"ev",    "project_name":"Fast-Charge Network", "budget_usd":450000, "co2_reduction_tons_per_year":780, "social_impact_score":7, "project_duration_months":28},
    {"id":"waste", "project_name":"Circular Materials",  "budget_usd":95000,  "co2_reduction_tons_per_year":240, "social_impact_score":8, "project_duration_months":16},
]

URL = "http://localhost:8000/api/v1/evaluate"
out = []
with httpx.Client(timeout=10) as c:
    for country, p in itertools.product(COUNTRIES, PRESETS):
        body = {**{k:v for k,v in p.items() if k!="id"}, "country":country, "region":country}
        r = c.post(URL, json=body)
        r.raise_for_status()
        d = r.json()
        out.append({
            "country": country, "preset": p["id"],
            "expected": {
                "total_score":   round(d["total_score"], 2),
                "environment_score": round(d["environment_score"], 2),
                "social_score":  round(d["social_score"], 2),
                "economic_score":round(d["economic_score"], 2),
                "risk_level":    d["risk_level"],
            }
        })
        print(f"  {country:<8} · {['id']:<6} · {d['total_score']:6.2f} · {d['risk_level']}")

Path("tests/baseline_scores.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
print(f"\n✅ Saved {len(out)} cases to tests/baseline_scores.json")
