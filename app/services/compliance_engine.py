from typing import Dict, List, Any
from app.schemas import ProjectInput

ESRS_WEIGHTS = {
    "E1_Climate": 0.30, "E3_Water": 0.15, "E4_Biodiversity": 0.15,
    "S1_Workforce": 0.20, "G1_Governance": 0.20,
}

def _status(s: float) -> str:
    return "ready" if s >= 80 else "partial" if s >= 60 else "gap"

def _e1(p):
    co2 = min(p.co2_reduction / 500.0, 1.0)
    dur = min(p.duration_months / 36.0, 1.0)
    s = round((co2 * 0.7 + dur * 0.3) * 100, 1)
    g = []
    if p.co2_reduction < 100: g.append("Low CO2 reduction - Scope 1+2 inventory required")
    if p.duration_months < 12: g.append("Short duration - transition plan not demonstrable")
    if s < 70: g.append("Missing Scope 3 emissions disclosure")
    return {"score": s, "status": _status(s), "gaps": g}

def _e3(p):
    cat = (p.category or "").lower()
    base = 80.0 if "water" in cat else 55.0 if ("solar" in cat or "wind" in cat) else 45.0
    s = min(round(base + min(p.social_impact, 10) * 1.5, 1), 100.0)
    g = []
    if "water" not in cat: g.append("Missing water risk assessment")
    if s < 60: g.append("No water consumption baseline")
    return {"score": s, "status": _status(s), "gaps": g}

def _e4(p):
    cat = (p.category or "").lower()
    base = 85.0 if ("refor" in cat or "biodivers" in cat) else 50.0
    if p.lat is not None and p.lon is not None: base += 5.0
    s = min(base, 100.0)
    g = []
    if "refor" not in cat and "biodivers" not in cat:
        g.append("No biodiversity impact assessment")
    if s < 70: g.append("Missing site-level ecosystem analysis")
    return {"score": s, "status": _status(s), "gaps": g}

def _s1(p):
    s = round(min(p.social_impact, 10) * 10.0, 1)
    g = []
    if p.social_impact < 6: g.append("Low social impact - workforce policies not documented")
    if s < 80: g.append("Missing supplier code of conduct (S2)")
    return {"score": s, "status": _status(s), "gaps": g}

def _g1(p):
    bn = min(p.budget / 500000.0, 1.0)
    s = round(60.0 + bn * 30.0, 1)
    g = []
    if p.budget < 50000: g.append("Low budget - governance framework insufficient")
    if s < 80: g.append("No formal anti-corruption policy disclosed")
    return {"score": s, "status": _status(s), "gaps": g}

def assess_csrd(p: ProjectInput) -> Dict[str, Any]:
    cats = {
        "E1_Climate": _e1(p),
        "E3_Water": _e3(p),
        "E4_Biodiversity": _e4(p),
        "S1_Workforce": _s1(p),
        "G1_Governance": _g1(p),
    }
    overall = round(sum(cats[k]["score"] * ESRS_WEIGHTS[k] for k in cats), 1)
    all_gaps: List[str] = []
    for c in cats.values():
        all_gaps.extend(c["gaps"])
    sorted_cats = sorted(cats.items(), key=lambda kv: kv[1]["score"])
    actions = []
    for name, d in sorted_cats[:3]:
        if d["gaps"]:
            actions.append({
                "category": name,
                "priority": "high" if d["score"] < 60 else "medium",
                "action": d["gaps"][0],
                "current_score": d["score"],
            })
    return {
        "framework": "CSRD_ESRS",
        "framework_version": "Post-Omnibus I (2026)",
        "project_name": p.name,
        "region": p.region,
        "overall_readiness": overall,
        "status": _status(overall),
        "categories": cats,
        "weights": ESRS_WEIGHTS,
        "missing_evidence": all_gaps,
        "recommended_actions": actions,
        "audit_ready": overall >= 80 and not any(c["status"] == "gap" for c in cats.values()),
    }
