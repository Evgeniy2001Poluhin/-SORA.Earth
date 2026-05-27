"""SORA.Earth - LLM Co-Pilot for ESG prediction explanations."""
import os
from typing import Optional

FEATURE_RU = {
    "budget": "budget",
    "co2_reduction": "CO2 reduction",
    "social_impact": "social impact",
    "duration_months": "duration (months)",
    "budget_per_month": "budget per month",
    "co2_per_dollar": "CO2 per dollar",
    "efficiency_score": "efficiency score",
    "impact_ratio": "impact ratio",
    "budget_efficiency": "budget efficiency",
    "category_enc": "category",
    "region_enc": "region",
}


def _verdict(p):
    if p >= 0.85:
        return {"label": "Very high success probability", "level": "excellent"}
    if p >= 0.70:
        return {"label": "High success probability", "level": "high"}
    if p >= 0.50:
        return {"label": "Moderate success probability", "level": "moderate"}
    if p >= 0.30:
        return {"label": "Low success probability", "level": "low"}
    return {"label": "Very low success probability", "level": "critical"}


def _confidence(p):
    distance = abs(p - 0.5) * 2
    if distance > 0.6:
        return "high"
    if distance > 0.3:
        return "medium"
    return "low"


def _drivers_from_shap(shap_values):
    if not shap_values:
        return {"positive": [], "negative": []}
    sorted_vals = sorted(shap_values, key=lambda x: abs(float(x.get("shap_value", 0))), reverse=True)
    pos = [x for x in sorted_vals if float(x.get("shap_value", 0)) > 0][:3]
    neg = [x for x in sorted_vals if float(x.get("shap_value", 0)) < 0][:3]

    def _fmt(items, sign):
        return [{
            "feature": x.get("feature"),
            "feature_label": FEATURE_RU.get(x.get("feature", ""), x.get("feature")),
            "shap_value": round(float(x.get("shap_value", 0)), 4),
            "direction": "increases" if sign > 0 else "decreases",
        } for x in items]

    return {"positive": _fmt(pos, +1), "negative": _fmt(neg, -1)}


def _drivers_from_features(features):
    pos, neg = [], []
    if features.get("co2_reduction", 0) > 5000:
        pos.append({"feature": "co2_reduction", "feature_label": "CO2 reduction", "note": "high (>5000)"})
    if features.get("social_impact", 0) > 70:
        pos.append({"feature": "social_impact", "feature_label": "social impact", "note": "high (>70)"})
    if features.get("budget", 0) > 5_000_000:
        neg.append({"feature": "budget", "feature_label": "budget", "note": "very high (>5M)"})
    if features.get("duration_months", 0) > 24:
        neg.append({"feature": "duration_months", "feature_label": "duration", "note": "long (>24 months)"})
    return {"positive": pos[:3], "negative": neg[:3]}


def _recommendation(level, confidence):
    if level in ("excellent", "high"):
        if confidence == "high":
            return "Recommended for funding. Model is confident in success."
        return "Recommended for funding with additional review."
    if level == "moderate":
        return "Requires deeper analysis. Consider optimizing key factors."
    if level == "low":
        return "High risk. Project refinement recommended before funding."
    return "Not recommended for funding in current form."


def _risks(features, drivers):
    risks = []
    if features.get("budget", 0) > 5_000_000:
        risks.append("Very high budget increases financial risk")
    if features.get("duration_months", 0) > 24:
        risks.append("Long timeline increases uncertainty")
    if features.get("co2_per_dollar", 0) < 0.5:
        risks.append("Low environmental efficiency per dollar invested")
    for d in drivers.get("negative", [])[:2]:
        if "feature_label" in d:
            risks.append("Negative factor: " + str(d["feature_label"]))
    return risks[:5]


def explain_prediction(probability, features, shap_values=None, project=None, model_version="v5"):
    verdict = _verdict(probability)
    confidence = _confidence(probability)
    drivers = _drivers_from_shap(shap_values) if shap_values else _drivers_from_features(features)
    risks = _risks(features, drivers)
    recommendation = _recommendation(verdict["level"], confidence)

    response = {
        "verdict": verdict,
        "probability": round(probability, 4),
        "confidence": confidence,
        "key_drivers": drivers,
        "risks": risks,
        "recommendation": recommendation,
        "model_version": model_version,
        "explanation_mode": "template",
    }

    if os.getenv("OPENAI_API_KEY"):
        try:
            response = _enrich_with_gpt(response, features, project)
            response["explanation_mode"] = "gpt"
        except Exception as e:
            response["llm_error"] = str(e)

    return response


def _enrich_with_gpt(base, features, project):
    import openai
    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    prompt = (
        "Rewrite this ESG project prediction explanation in natural language. "
        "Verdict: " + base["verdict"]["label"] + ". "
        "Probability: " + str(base["probability"]) + ". "
        "Drivers: " + str(base["key_drivers"]) + ". "
        "Risks: " + str(base["risks"]) + ". "
        "Give 2-3 sentence executive summary."
    )
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=300,
    )
    base["executive_summary"] = resp.choices[0].message.content.strip()
    return base


def health():
    return {
        "ok": True,
        "llm_enabled": bool(os.getenv("OPENAI_API_KEY")),
        "explanation_mode": "gpt" if os.getenv("OPENAI_API_KEY") else "template",
        "supported_features": list(FEATURE_RU.keys()),
    }
