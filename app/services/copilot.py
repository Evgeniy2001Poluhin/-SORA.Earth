"""SORA.Earth - LLM Co-Pilot for ESG prediction explanations."""
import os
import logging
from typing import Optional

log = logging.getLogger(__name__)

# Claude model for Co-Pilot explanations. Default to Haiku 4.5 (fast + cheap,
# well-suited to high-volume real-time explanations); set ANTHROPIC_MODEL to
# "claude-sonnet-5" for higher-quality narratives. NOTE: the legacy
# "claude-3-5-haiku" / "claude-3-5-sonnet" IDs are retired and 404 — these are
# their current equivalents.
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5")

_COPILOT_SYSTEM = (
    "You are SORA.earth Co-Pilot, an ESG investment analyst. You are given a "
    "machine-learning success prediction for a sustainable project together with its "
    "explainability drivers (SHAP values) and concrete project metrics. Write a concise, "
    "natural-language executive summary for a decision-maker: explain what is driving the "
    "prediction, reference the specific metrics and drivers you were given, and be specific "
    "to THIS project — never generic boilerplate. Do not invent numbers that were not "
    "provided. 2-4 sentences, professional and direct."
)


def _anthropic_enabled() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _fmt_drivers(items) -> str:
    lines = []
    for d in items or []:
        label = d.get("feature_label") or d.get("feature") or "?"
        sv = d.get("shap_value")
        if sv is not None:
            lines.append(f"- {label}: SHAP {float(sv):+.4f} ({d.get('direction', '')})".rstrip())
        elif d.get("note"):
            lines.append(f"- {label}: {d['note']}")
        else:
            lines.append(f"- {label}")
    return "\n".join(lines) or "- (none identified)"


def _explanation_prompt(base, features, project, probability) -> str:
    """Build the user prompt from real project data + SHAP drivers."""
    features = features or {}
    drivers = base.get("key_drivers", {}) if isinstance(base, dict) else {}
    proj = project or {}
    name = proj.get("project_name") or proj.get("name") or "this ESG project"
    risks = base.get("risks", []) if isinstance(base, dict) else []
    return "\n".join([
        f"Project: {name}",
        f"Predicted success probability: {probability:.0%}",
        f"Verdict: {base['verdict']['label']} (model confidence: {base['confidence']})",
        "",
        "Project metrics:",
        f"- Budget: {features.get('budget', 'n/a')} USD",
        f"- CO2 reduction: {features.get('co2_reduction', 'n/a')} t/yr",
        f"- Social impact score: {features.get('social_impact', 'n/a')}",
        f"- Duration: {features.get('duration_months', 'n/a')} months",
        f"- CO2 per dollar: {features.get('co2_per_dollar', 'n/a')}",
        "",
        "Positive drivers (increase success probability):",
        _fmt_drivers(drivers.get("positive")),
        "",
        "Negative drivers (decrease success probability):",
        _fmt_drivers(drivers.get("negative")),
        "",
        "Identified risks:",
        "\n".join(f"- {r}" for r in risks) or "- (none)",
        "",
        "Write the executive summary now.",
    ])


def _generate_explanation_claude(base, features, project, probability, max_tokens=400) -> str:
    """Synchronous Claude call — returns a unique executive summary for this project."""
    import anthropic
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    msg = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        system=_COPILOT_SYSTEM,
        messages=[{"role": "user", "content": _explanation_prompt(base, features, project, probability)}],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()


async def stream_explanation_claude(base, features, project, probability, max_tokens=400):
    """Async generator yielding real text deltas from the Anthropic streaming API."""
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic()  # reads ANTHROPIC_API_KEY from the environment
    async with client.messages.stream(
        model=ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        system=_COPILOT_SYSTEM,
        messages=[{"role": "user", "content": _explanation_prompt(base, features, project, probability)}],
    ) as stream:
        async for text in stream.text_stream:
            yield text

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
    """Build the structured explanation scaffold, then (unless enrich=False) generate a
    natural-language executive summary via the real Anthropic API. Pass enrich=False to
    return only the deterministic structure — used by the streaming endpoint, which streams
    the summary live instead of pre-computing it."""
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

    if not enrich:
        return response

    if _anthropic_enabled():
        try:
            response["executive_summary"] = _generate_explanation_claude(response, features, project, probability)
            response["explanation_mode"] = "claude"
            response["llm_model"] = ANTHROPIC_MODEL
        except Exception as e:
            log.warning("Claude explanation failed: %s", e)
            response["llm_error"] = str(e)
    elif os.getenv("OPENAI_API_KEY"):
        try:
            response = _enrich_with_gpt(response, features, project)
            response["explanation_mode"] = "gpt"
        except Exception as e:
            response["llm_error"] = str(e)

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
    if _anthropic_enabled():
        mode = "claude"
    elif os.getenv("OPENAI_API_KEY"):
        mode = "gpt"
    else:
        mode = "template"
    return {
        "ok": True,
        "llm_enabled": _anthropic_enabled() or bool(os.getenv("OPENAI_API_KEY")),
        "explanation_mode": mode,
        "llm_model": ANTHROPIC_MODEL if _anthropic_enabled() else None,
        "supported_features": list(FEATURE_RU.keys()),
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
