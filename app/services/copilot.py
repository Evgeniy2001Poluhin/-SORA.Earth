"""SORA.Earth - Co-Pilot for ESG prediction explanations with smart templates."""
import os
import logging
from typing import Optional

log = logging.getLogger(__name__)

# Smart template-based explanations (replaces HuggingFace LLM)
from app.services.copilot_templates import generate_smart_explanation


def _hf_enabled() -> bool:
    """Legacy check - always False now (HuggingFace removed)."""
    return False


def _hf_headers() -> dict:
    """Legacy function - not used anymore."""
    return {}


def stream_explanation_hf(base, features, project, probability, max_new_tokens=500):
    """Legacy function for HuggingFace streaming - now returns template-based text.

    Kept for backwards compatibility with streaming endpoint.
    """
    # Generate smart explanation and yield it word-by-word for streaming effect
    try:
        smart_explanation = generate_smart_explanation(
            probability=probability,
            features=features,
            shap_values=None
        )
        summary = smart_explanation.get("executive_summary", "")
        if summary:
            # Yield word by word for streaming effect
            for word in summary.split(" "):
                yield word + " "
    except Exception as e:
        log.warning("Smart template streaming failed: %s", e)
        yield f"[Co-Pilot template generation failed: {e}]"

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


def explain_prediction(probability, features, shap_values=None, project=None, model_version="v5", enrich=True):
    """Build structured explanation using smart template-based system.

    The new system uses a scenario matrix (probability level × top negative factor)
    to generate contextually appropriate explanations without requiring an LLM.
    """
    verdict = _verdict(probability)
    confidence = _confidence(probability)
    drivers = _drivers_from_shap(shap_values) if shap_values else _drivers_from_features(features)

    response = {
        "verdict": verdict,
        "probability": round(probability, 4),
        "confidence": confidence,
        "key_drivers": drivers,
        "model_version": model_version,
        "explanation_mode": "smart_template",
    }

    if not enrich:
        return response

    # Generate smart template-based explanation
    try:
        smart_explanation = generate_smart_explanation(
            probability=probability,
            features=features,
            shap_values=shap_values
        )
        response.update(smart_explanation)
    except Exception as e:
        log.warning("Smart template generation failed: %s", e)
        # Fallback to legacy simple template
        response["risks"] = _risks(features, drivers)
        response["recommendation"] = _recommendation(verdict["level"], confidence)
        response["executive_summary"] = f"Project shows {verdict['label'].lower()} with {confidence} model confidence."

    return response



def _chat_completion(messages, max_tokens=350, temperature=0.4):
    """Call OpenRouter/OpenAI with model fallback. LLM_MODEL may be comma-separated."""
    import openai
    client = openai.OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    )
    models = [m.strip() for m in os.getenv("LLM_MODEL", "gpt-4o-mini").split(",") if m.strip()]
    last_err = None
    for model in models:
        try:
            return client.chat.completions.create(
                model=model, messages=messages,
                max_tokens=max_tokens, temperature=temperature,
            )
        except (openai.RateLimitError, openai.NotFoundError, openai.APIConnectionError) as e:
            last_err = e
            continue
    raise last_err if last_err else RuntimeError("No LLM model configured")


def _enrich_with_gpt(base, features, project):
    import openai
    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"), base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    prompt = (
        "Rewrite this ESG project prediction explanation in natural language. "
        "Verdict: " + base["verdict"]["label"] + ". "
        "Probability: " + str(base["probability"]) + ". "
        "Drivers: " + str(base["key_drivers"]) + ". "
        "Risks: " + str(base["risks"]) + ". "
        "Give 2-3 sentence executive summary."
    )
    resp = _chat_completion([{"role": "user", "content": prompt}], max_tokens=300, temperature=0.4)
    base["executive_summary"] = resp.choices[0].message.content.strip()
    return base


def health():
    """Health check for Co-Pilot service."""
    mode = "smart_template"
    if os.getenv("OPENAI_API_KEY"):
        mode = "smart_template_with_gpt_fallback"

    return {
        "ok": True,
        "llm_enabled": False,  # No LLM required anymore
        "explanation_mode": mode,
        "llm_model": None,  # Template-based, no LLM
        "supported_features": list(FEATURE_RU.keys()),
        "scenario_matrix": {
            "probability_levels": ["LOW", "MODERATE", "HIGH"],
            "negative_factors": ["budget", "duration_months", "co2_reduction", "social_impact", "efficiency_score", "country_gdp_per_capita"],
            "total_scenarios": 18,  # 3 levels × 6 factors
        }
    }


# ============ Day 7: Audience presets + QA ============

_AUDIENCE_DIRECTIVES = {
    "executive": "Provide a concise executive summary with key drivers and a clear recommendation.",
    "investor":  "Focus on ROI: CO2 per dollar, payback, comparable benchmarks. End with explicit FUND / DO NOT FUND verdict and confidence level.",
    "auditor":   "Adopt a conservative, formal tone. Cite policy alignment, compliance risks, data lineage, and caveats. State all assumptions explicitly.",
    "operator":  "Focus on execution: timeline risks, KPIs to monitor, mitigation actions. Output 3-5 actionable next steps.",
}


def audience_directive(audience: str) -> str:
    return _AUDIENCE_DIRECTIVES.get((audience or "executive").lower(),
                           _AUDIENCE_DIRECTIVES["executive"])


def answer_qa(question: str, context: str, sources: list, audience: str = "executive") -> dict:
    """Follow-up QA in a Co-Pilot session. Uses GPT if available, otherwise deterministic fallback."""
    src_block = ""
    if sources:
        src_block = "\n\nRetrieved knowledge:\n" + "\n".join(
            f"- [{s.get('id','?')}] {s.get('title','')}: {s.get('excerpt','')[:200]}"
            for s in sources[:4]
        )

    if not os.getenv("OPENAI_API_KEY"):
        ans = (f"Based on the prior explanation and {len(sources)} retrieved sources, "
               f"the answer to '{question}' depends on the key drivers already identified. "
               f"Audience: {audience}. {audience_directive(audience)}")
        return {"answer": ans, "mode": "template", "tokens_used": 0}

    import openai
    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"), base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    system = (
        "You are SORA.earth Co-Pilot, answering follow-up questions about an ESG project prediction. "
        + audience_directive(audience)
        + " Cite sources by their [doc_id] when relevant. Keep under 180 words."
    )
    user = (
        f"Prior explanation context:\n{context[:1500]}"
        f"{src_block}\n\nUser question: {question}"
    )
    resp = _chat_completion(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=350, temperature=0.4,
    )
    txt = resp.choices[0].message.content.strip()
    usage = getattr(resp, "usage", None)
    tokens = getattr(usage, "total_tokens", 0) if usage else 0
    return {"answer": txt, "mode": "gpt", "tokens_used": tokens}
