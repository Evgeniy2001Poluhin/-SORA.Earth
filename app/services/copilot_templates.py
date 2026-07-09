"""Smart template-based Co-Pilot explanations with scenario matrix."""
from typing import Dict, List, Any, Optional

# Scenario matrix: probability levels
PROBABILITY_LEVELS = {
    "LOW": (0.0, 0.30),
    "MODERATE": (0.30, 0.60),
    "HIGH": (0.60, 1.0),
}


def get_probability_level(probability: float) -> str:
    """Classify probability into LOW/MODERATE/HIGH."""
    if probability < 0.30:
        return "LOW"
    elif probability < 0.60:
        return "MODERATE"
    else:
        return "HIGH"


# Template responses by (probability_level, top_negative_factor)
SCENARIO_TEMPLATES = {
    # LOW probability scenarios
    ("LOW", "budget"): {
        "summary": "This project shows concerning viability ({prob:.0%} success probability). The primary constraint is budget allocation efficiency. With a budget of ${budget:,.0f}, the capital intensity significantly impacts the predicted outcome.",
        "recommendation": "Consider budget optimization: evaluate component costs, explore alternative suppliers, or consider phased implementation to reduce upfront capital requirements.",
        "risks": ["High capital intensity reduces ROI potential", "Budget efficiency below sustainable threshold", "Financial risk may exceed typical ESG project parameters"],
    },
    ("LOW", "duration_months"): {
        "summary": "This project faces a challenging timeline ({prob:.0%} success probability). The {duration} month duration creates substantial execution uncertainty and compounds risk exposure.",
        "recommendation": "Accelerate timeline through parallel workstreams, milestone-based delivery, or consider scope reduction to achieve faster time-to-impact.",
        "risks": ["Extended timeline increases uncertainty and cost overruns", "Long duration reduces adaptability to changing conditions", "Delayed ROI may impact stakeholder confidence"],
    },
    ("LOW", "co2_reduction"): {
        "summary": "The environmental impact potential is below expectations ({prob:.0%} success probability). With only {co2:,.0f} tonnes CO2 reduction annually, the climate benefit does not justify the investment scale.",
        "recommendation": "Enhance CO2 impact through technology upgrades, expanded scope, or supplementary carbon offset mechanisms to improve environmental ROI.",
        "risks": ["Insufficient environmental impact for certification", "May not meet corporate sustainability targets", "Low CO2 efficiency reduces ESG rating potential"],
    },
    ("LOW", "social_impact"): {
        "summary": "The social value proposition is underdeveloped ({prob:.0%} success probability). A social impact score of {social:.1f}/10 suggests limited community benefit or stakeholder engagement.",
        "recommendation": "Strengthen social component: increase local job creation, enhance community consultation, or integrate capacity-building programs.",
        "risks": ["Weak stakeholder buy-in may cause delays", "Limited social license to operate", "May fail social impact assessment criteria"],
    },
    ("LOW", "efficiency_score"): {
        "summary": "The project's operational efficiency is critically low ({prob:.0%} success probability). The combined CO2-to-cost and social impact efficiency metrics indicate suboptimal resource utilization.",
        "recommendation": "Fundamental redesign required: optimize technology stack, improve operational efficiency, or re-evaluate project architecture for better impact-per-dollar.",
        "risks": ["Poor efficiency suggests structural design flaws", "May not compete with alternative projects", "Low efficiency reduces scalability potential"],
    },
    ("LOW", "country_gdp_per_capita"): {
        "summary": "The macroeconomic context presents significant challenges ({prob:.0%} success probability). Operating in a region with GDP per capita of ${gdp:,.0f} introduces execution and financing constraints.",
        "recommendation": "Adapt to local context: secure development finance partnerships, implement capacity-building, or explore public-private partnership structures.",
        "risks": ["Local financing capacity may be limited", "Institutional support infrastructure may be underdeveloped", "Currency and macroeconomic volatility risks"],
    },
    ("LOW", "default"): {
        "summary": "This project shows low viability ({prob:.0%} success probability). Multiple factors contribute to the elevated risk profile.",
        "recommendation": "Comprehensive project redesign recommended before proceeding. Address key constraints identified in the analysis.",
        "risks": ["Multiple risk factors compound uncertainty", "Current design unlikely to meet success criteria", "Requires fundamental re-evaluation"],
    },

    # MODERATE probability scenarios
    ("MODERATE", "budget"): {
        "summary": "This project shows moderate viability ({prob:.0%} success probability). Budget allocation at ${budget:,.0f} is workable but could be optimized for better cost efficiency.",
        "recommendation": "Refine budget allocation: prioritize high-impact components, negotiate volume discounts, or explore innovative financing to reduce capital intensity.",
        "risks": ["Budget constraints may limit project scope flexibility", "Cost overruns would significantly impact viability", "Moderate capital efficiency requires careful monitoring"],
    },
    ("MODERATE", "duration_months"): {
        "summary": "The project timeline ({duration} months) creates moderate uncertainty ({prob:.0%} success probability). While feasible, the duration introduces material execution risk.",
        "recommendation": "Optimize timeline: establish aggressive milestones, build in buffer for critical path items, and implement stage-gate reviews to maintain momentum.",
        "risks": ["Timeline overruns are statistically likely at this duration", "External factors may impact long-duration projects", "Maintaining stakeholder engagement over extended timeline"],
    },
    ("MODERATE", "co2_reduction"): {
        "summary": "The environmental impact ({co2:,.0f} tonnes CO2/year) is adequate but not exceptional ({prob:.0%} success probability). There's opportunity to enhance the climate benefit.",
        "recommendation": "Consider CO2 impact enhancements: upgrade to more efficient technology, expand project scope, or add complementary carbon reduction measures.",
        "risks": ["CO2 targets may be challenging to achieve", "Environmental certification thresholds may not be met", "Competitors may offer superior environmental ROI"],
    },
    ("MODERATE", "social_impact"): {
        "summary": "The social impact score ({social:.1f}/10) is moderate ({prob:.0%} success probability). Stakeholder value is present but could be strengthened for better project resilience.",
        "recommendation": "Enhance social component: increase community co-benefits, improve stakeholder engagement processes, or add workforce development initiatives.",
        "risks": ["Moderate social buy-in may cause implementation friction", "May face resistance from key stakeholder groups", "Social impact measurement may be difficult to verify"],
    },
    ("MODERATE", "efficiency_score"): {
        "summary": "Operational efficiency is adequate but leaves room for improvement ({prob:.0%} success probability). The impact-to-cost ratio suggests value but not optimal returns.",
        "recommendation": "Efficiency improvements: streamline operations, adopt best practices from similar projects, or re-engineer high-cost/low-impact components.",
        "risks": ["Efficiency gaps may compound over project lifecycle", "May not meet internal hurdle rates", "Operational improvements needed to ensure viability"],
    },
    ("MODERATE", "country_gdp_per_capita"): {
        "summary": "The regional economic context (GDP/capita ${gdp:,.0f}) creates moderate execution complexity ({prob:.0%} success probability). Local capacity requires careful consideration.",
        "recommendation": "Mitigate country risk: engage local partners with proven track records, secure export credit agency support, or implement adaptive management frameworks.",
        "risks": ["Local institutional capacity may vary", "Economic volatility could impact project economics", "Requires robust risk mitigation strategies"],
    },
    ("MODERATE", "default"): {
        "summary": "This project shows moderate viability ({prob:.0%} success probability). With targeted improvements, success probability can be enhanced.",
        "recommendation": "Focus on optimizing the key constraint factors identified in the analysis. Selective improvements will significantly boost success likelihood.",
        "risks": ["Moderate risk profile requires active management", "Success depends on execution quality", "Monitor key metrics closely during implementation"],
    },

    # HIGH probability scenarios
    ("HIGH", "budget"): {
        "summary": "This project demonstrates strong fundamentals ({prob:.0%} success probability). Budget allocation of ${budget:,.0f} is well-calibrated, though minor optimizations could further improve efficiency.",
        "recommendation": "Proceed with confidence. Consider value engineering for non-critical components to free up budget for impact amplification or contingency reserves.",
        "risks": ["Minor cost overrun risk remains", "Opportunity cost if budget could be allocated more efficiently", "Monitor for scope creep that could erode margins"],
    },
    ("HIGH", "duration_months"): {
        "summary": "Strong project viability ({prob:.0%} success probability) with a manageable {duration}-month timeline. Duration is appropriate for the scope and presents low execution risk.",
        "recommendation": "Recommended for approval. Maintain disciplined project management to preserve timeline advantages. Consider accelerating high-value early wins.",
        "risks": ["Standard project management risks apply", "External dependencies should be actively managed", "Timeline buffers should be preserved for unforeseen events"],
    },
    ("HIGH", "co2_reduction"): {
        "summary": "Excellent environmental credentials ({prob:.0%} success probability). The {co2:,.0f} tonnes CO2/year reduction represents meaningful climate impact and positions this project well for ESG financing.",
        "recommendation": "Strong candidate for funding. Consider expanding scope to maximize environmental leverage, or use as reference case for portfolio development.",
        "risks": ["CO2 measurement and verification protocols must be rigorous", "Carbon price volatility may impact economic case", "Maintain additionality to qualify for carbon credits"],
    },
    ("HIGH", "social_impact"): {
        "summary": "Impressive social impact profile (score: {social:.1f}/10) drives strong success probability ({prob:.0%}). Community co-benefits create project resilience and stakeholder support.",
        "recommendation": "Recommended for funding. Document social impact methodology rigorously to support replication and demonstrate sector leadership.",
        "risks": ["Maintain authentic stakeholder engagement throughout lifecycle", "Social impact claims must be independently verifiable", "Expectations management with community stakeholders"],
    },
    ("HIGH", "efficiency_score"): {
        "summary": "Outstanding efficiency metrics underpin strong success probability ({prob:.0%}). The impact-to-cost ratio represents excellent value and positions this as a benchmark project.",
        "recommendation": "Strongly recommended. Consider fast-tracking approval and using efficiency framework as template for future projects.",
        "risks": ["High efficiency creates reputational risk if not achieved", "Maintain quality standards while preserving efficiency gains", "Document efficiency drivers for knowledge transfer"],
    },
    ("HIGH", "country_gdp_per_capita"): {
        "summary": "Favorable macroeconomic environment (GDP/capita ${gdp:,.0f}) supports strong project success probability ({prob:.0%}). Local institutional capacity reduces execution risk.",
        "recommendation": "Recommended for funding. Leverage local strengths: strong institutions, financing access, and technical capacity to accelerate implementation.",
        "risks": ["Country advantages may mask project-specific risks", "Ensure project is not overly dependent on favorable macro conditions", "Standard due diligence still applies"],
    },
    ("HIGH", "default"): {
        "summary": "This project shows strong viability ({prob:.0%} success probability). Fundamentals are solid across key dimensions.",
        "recommendation": "Recommended for funding. Maintain execution discipline and monitor key performance indicators to preserve success trajectory.",
        "risks": ["Standard project execution risks apply", "Complacency risk given strong fundamentals", "Continue active risk management throughout lifecycle"],
    },
}


def get_top_negative_factor(shap_values: Optional[List[Dict[str, Any]]]) -> str:
    """Identify the top negative SHAP factor."""
    if not shap_values:
        return "default"

    negative = [
        sv for sv in shap_values
        if float(sv.get("shap_value", 0)) < 0
    ]

    if not negative:
        return "default"

    # Sort by absolute SHAP value (most negative first)
    negative_sorted = sorted(
        negative,
        key=lambda x: abs(float(x.get("shap_value", 0))),
        reverse=True
    )

    top_feature = negative_sorted[0].get("feature", "default")

    # Map to known factor categories
    factor_map = {
        "budget": "budget",
        "duration_months": "duration_months",
        "co2_reduction": "co2_reduction",
        "social_impact": "social_impact",
        "efficiency_score": "efficiency_score",
        "country_gdp_per_capita": "country_gdp_per_capita",
        "budget_per_month": "budget",
        "co2_per_dollar": "efficiency_score",
        "budget_efficiency": "efficiency_score",
        "impact_ratio": "efficiency_score",
    }

    return factor_map.get(top_feature, "default")


def format_template(template: str, probability: float, features: Dict[str, Any]) -> str:
    """Format a template string with actual project data."""
    return template.format(
        prob=probability,
        budget=features.get("budget", 0),
        duration=int(features.get("duration_months", 0)),
        co2=features.get("co2_reduction", 0),
        social=features.get("social_impact", 0),
        gdp=features.get("country_gdp_per_capita", 12720),
    )


def generate_smart_explanation(
    probability: float,
    features: Dict[str, Any],
    shap_values: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """Generate smart template-based explanation using scenario matrix."""

    # 1. Determine probability level
    prob_level = get_probability_level(probability)

    # 2. Identify top negative factor
    top_negative = get_top_negative_factor(shap_values)

    # 3. Select template
    template_key = (prob_level, top_negative)
    template = SCENARIO_TEMPLATES.get(
        template_key,
        SCENARIO_TEMPLATES.get((prob_level, "default"))
    )

    # 4. Format with actual data
    executive_summary = format_template(template["summary"], probability, features)
    recommendation = format_template(template["recommendation"], probability, features)
    risks = [format_template(r, probability, features) for r in template["risks"]]

    return {
        "executive_summary": executive_summary,
        "recommendation": recommendation,
        "risks": risks,
        "scenario": {
            "probability_level": prob_level,
            "top_negative_factor": top_negative,
            "template_key": f"{prob_level}_{top_negative}",
        }
    }
