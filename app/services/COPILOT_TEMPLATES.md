# Co-Pilot Smart Template System

## Overview

The SORA.Earth Co-Pilot now uses a **smart template-based explanation system** instead of LLM (HuggingFace/GPT) generation. This provides:

- ✅ **Zero latency** - instant responses without API calls
- ✅ **Zero cost** - no LLM API fees
- ✅ **100% predictable** - consistent, reliable explanations
- ✅ **Fully offline** - no external dependencies
- ✅ **Domain-optimized** - ESG-specific language and insights

## Architecture

### Scenario Matrix

The system uses a **2-dimensional scenario matrix**:

```
probability_level × top_negative_factor → template
```

**Probability Levels (3):**
- `LOW`: 0-30% success probability
- `MODERATE`: 30-60% success probability  
- `HIGH`: 60-100% success probability

**Top Negative Factors (6):**
- `budget` - budget allocation efficiency
- `duration_months` - project timeline
- `co2_reduction` - environmental impact
- `social_impact` - community benefits
- `efficiency_score` - operational efficiency
- `country_gdp_per_capita` - macroeconomic context

**Total scenarios:** 3 × 6 = **18 unique templates** (+ 3 "default" fallbacks)

### Template Structure

Each template contains:

```python
{
    "summary": "Executive summary with {placeholders}",
    "recommendation": "Actionable recommendation",
    "risks": ["Risk 1", "Risk 2", "Risk 3"]
}
```

Placeholders are filled with actual project data:
- `{prob}` → probability as percentage
- `{budget}` → budget with comma formatting
- `{duration}` → duration in months
- `{co2}` → CO2 reduction tonnes/year
- `{social}` → social impact score
- `{gdp}` → country GDP per capita

## Example Flow

### Input
```python
probability = 0.25  # 25% success
features = {"budget": 500000, "co2_reduction": 100, "duration_months": 12}
shap_values = [
    {"feature": "budget", "shap_value": -0.20},  # Strongest negative
    {"feature": "co2_reduction", "shap_value": 0.05}
]
```

### Processing
1. **Classify probability:** 0.25 → `LOW`
2. **Identify top negative:** budget has SHAP -0.20 → `budget`
3. **Select template:** `(LOW, budget)` → budget optimization template
4. **Format with data:** Fill placeholders with actual values

### Output
```python
{
    "executive_summary": "This project shows concerning viability (25% success probability). The primary constraint is budget allocation efficiency. With a budget of $500,000, the capital intensity significantly impacts the predicted outcome.",
    
    "recommendation": "Consider budget optimization: evaluate component costs, explore alternative suppliers, or consider phased implementation to reduce upfront capital requirements.",
    
    "risks": [
        "High capital intensity reduces ROI potential",
        "Budget efficiency below sustainable threshold", 
        "Financial risk may exceed typical ESG project parameters"
    ],
    
    "scenario": {
        "probability_level": "LOW",
        "top_negative_factor": "budget",
        "template_key": "LOW_budget"
    }
}
```

## Template Design Principles

### 1. Data-Driven Specificity
✅ **Good:** "With a budget of $500,000, the capital intensity..."  
❌ **Bad:** "The budget is too high..."

### 2. Actionable Recommendations
✅ **Good:** "Consider budget optimization: evaluate component costs, explore alternative suppliers..."  
❌ **Bad:** "The budget should be reduced."

### 3. Risk Contextualization
✅ **Good:** "High capital intensity reduces ROI potential"  
❌ **Bad:** "There are budget risks"

### 4. Probability-Appropriate Tone
- **LOW:** "concerning", "requires redesign", "not recommended"
- **MODERATE:** "adequate but...", "could be improved", "requires review"
- **HIGH:** "strong fundamentals", "recommended", "excellent"

## Integration Points

### 1. Main Explain Endpoint
`POST /api/v1/copilot/explain`

```python
from app.services.copilot import explain_prediction

response = explain_prediction(
    probability=0.45,
    features={"budget": 200000, "co2_reduction": 150, ...},
    shap_values=[...],
    enrich=True  # Generate smart template explanation
)
```

### 2. Streaming Endpoint
`POST /api/v1/copilot/explain/stream`

Streams template text word-by-word for visual effect (no actual LLM streaming).

### 3. Health Check
`GET /api/v1/copilot/health`

Returns:
```json
{
    "ok": true,
    "llm_enabled": false,
    "explanation_mode": "smart_template",
    "llm_model": null,
    "scenario_matrix": {
        "probability_levels": ["LOW", "MODERATE", "HIGH"],
        "negative_factors": [...],
        "total_scenarios": 18
    }
}
```

## Adding New Templates

To add a new factor or modify templates:

1. Edit `app/services/copilot_templates.py`
2. Add entries to `SCENARIO_TEMPLATES` dict:

```python
SCENARIO_TEMPLATES = {
    # ...existing templates...
    
    ("LOW", "new_factor"): {
        "summary": "Template with {prob} and {new_metric}",
        "recommendation": "Specific advice for this scenario",
        "risks": ["Risk 1", "Risk 2", "Risk 3"],
    },
}
```

3. Update `get_top_negative_factor()` if needed for factor mapping
4. Add tests in `tests/test_copilot_templates.py`

## Performance

**Before (HuggingFace LLM):**
- Latency: ~2-5 seconds
- Cost: ~$0.002 per explanation
- Requires: API token, internet, external service

**After (Smart Templates):**
- Latency: <10ms
- Cost: $0
- Requires: nothing

**Improvement:** 200-500x faster, 100% cost reduction

## Migration Notes

### Removed Dependencies
- ❌ `HF_API_TOKEN` environment variable (no longer needed)
- ❌ `HF_MODEL_URL` configuration
- ❌ `requests` library for HuggingFace API calls
- ❌ `stream_explanation_hf()` actual streaming (now word-paced template)

### Backwards Compatibility
- ✅ All API endpoints maintain same interface
- ✅ Response structure unchanged (`executive_summary`, `recommendation`, `risks`)
- ✅ `explanation_mode` now returns `"smart_template"` instead of `"huggingface"`
- ✅ Streaming endpoint works identically (word-paced effect)

### Testing
Run template-specific tests:
```bash
pytest tests/test_copilot_templates.py -v
```

Run all Co-Pilot tests:
```bash
pytest tests/ -k copilot -v
```

## Future Enhancements

Potential improvements:

1. **Multi-language support** - translate templates to Spanish, French, etc.
2. **Audience presets** - different templates for investors vs. operators
3. **Custom templates** - allow users to define organization-specific language
4. **A/B testing** - track which template variants drive better outcomes
5. **Learning from feedback** - refine templates based on user ratings

## Files Modified

- `app/services/copilot.py` - Core Co-Pilot logic
- `app/services/copilot_templates.py` - New template system (NEW)
- `app/api/copilot_api.py` - API endpoints
- `tests/test_copilot_templates.py` - Template tests (NEW)
