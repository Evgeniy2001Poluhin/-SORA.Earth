"""
Builds tests/baseline_scores.json — calibration snapshot of scoring engine.
Run once when scoring formulas change & you want a new baseline.

Usage:
    python tests/build_baseline.py
"""
import httpx
import json
import itertools
from pathlib import Path

COUNTRIES = ["Sweden", "Norway", "Germany", "USA", "Brazil", "India", "Kenya"]

PRESETS = {
    "solar":         {"budget": 150000, "co2_reduction": 340, "social_impact": 9, "duration_months": 18, "name": "Solar"},
    "wind":          {"budget": 280000, "co2_reduction": 620, "social_impact": 8, "duration_months": 24, "name": "Wind"},
    "reforestation": {"budget": 90000,  "co2_reduction": 280, "social_impact": 7, "duration_months": 36, "name": "Reforestation"},
    "water":         {"budget": 120000, "co2_reduction": 180, "social_impact": 9, "duration_months": 20, "name": "Water"},
    "ev":            {"budget": 350000, "co2_reduction": 780, "social_impact": 7, "duration_months": 28, "name": "EV"},
    "waste":         {"budget": 100000, "co2_reduction": 240, "social_impact": 8, "duration_months": 16, "name": "Waste"},
}

URL = "http://localhost:8000/api/v1/evaluate"


def main():
    baseline = {}
    failed = []
    for country, (preset_name, preset) in itertools.product(COUNTRIES, PRESETS.items()):
        payload = {**preset, "region": country}
        r = None
        for attempt in range(3):
            try:
                r = httpx.post(URL, json=payload, timeout=30).json()
                break
            except Exception:
                if attempt == 2: raise
            key = f"{country}_{preset_name}"
            baseline[key] = {
                "total_score": round(r["total_score"], 2),
                "environment_score": round(r["environment_score"], 2),
                "social_score": round(r["social_score"], 2),
                "economic_score": round(r["economic_score"], 2),
                "risk_level": r["risk_level"],
            }
            print(f"  [OK] {key}: total={baseline[key]['total_score']:.2f} risk={baseline[key]['risk_level']}")
        except Exception as e:
            failed.append((country, preset_name, str(e)))
            print(f"  [FAIL] {country}_{preset_name}: {e}")

    out = Path(__file__).parent / "baseline_scores.json"
    out.write_text(json.dumps(baseline, indent=2))
    print(f"\nSaved {len(baseline)} cases to {out}")
    if failed:
        print(f"\n{len(failed)} cases FAILED:")
        for c, p, e in failed:
            print(f"  - {c}_{p}: {e}")


if __name__ == "__main__":
    main()
