"""Tests for smart template-based Co-Pilot explanations."""
import pytest
from app.services.copilot_templates import (
    get_probability_level,
    get_top_negative_factor,
    generate_smart_explanation,
)


def test_probability_level_classification():
    """Test probability level classification."""
    assert get_probability_level(0.15) == "LOW"
    assert get_probability_level(0.29) == "LOW"
    assert get_probability_level(0.30) == "MODERATE"
    assert get_probability_level(0.45) == "MODERATE"
    assert get_probability_level(0.59) == "MODERATE"
    assert get_probability_level(0.60) == "HIGH"
    assert get_probability_level(0.85) == "HIGH"


def test_top_negative_factor_detection():
    """Test identification of top negative SHAP factor."""
    shap_values = [
        {"feature": "budget", "shap_value": -0.15},
        {"feature": "co2_reduction", "shap_value": 0.10},
        {"feature": "duration_months", "shap_value": -0.05},
    ]
    assert get_top_negative_factor(shap_values) == "budget"

    shap_values_duration = [
        {"feature": "budget", "shap_value": 0.10},
        {"feature": "duration_months", "shap_value": -0.25},
        {"feature": "co2_reduction", "shap_value": -0.05},
    ]
    assert get_top_negative_factor(shap_values_duration) == "duration_months"


def test_top_negative_factor_default():
    """Test default when no negative factors."""
    shap_values = [
        {"feature": "budget", "shap_value": 0.15},
        {"feature": "co2_reduction", "shap_value": 0.10},
    ]
    assert get_top_negative_factor(shap_values) == "default"
    assert get_top_negative_factor(None) == "default"
    assert get_top_negative_factor([]) == "default"


def test_generate_low_probability_budget_scenario():
    """Test LOW probability + budget constraint scenario."""
    features = {
        "budget": 500000,
        "co2_reduction": 100,
        "social_impact": 5,
        "duration_months": 12,
    }
    shap_values = [
        {"feature": "budget", "shap_value": -0.20},
        {"feature": "co2_reduction", "shap_value": 0.05},
    ]

    result = generate_smart_explanation(0.25, features, shap_values)

    assert "executive_summary" in result
    assert "recommendation" in result
    assert "risks" in result
    assert "scenario" in result
    assert result["scenario"]["probability_level"] == "LOW"
    assert result["scenario"]["top_negative_factor"] == "budget"
    assert "budget" in result["executive_summary"].lower()
    assert "500,000" in result["executive_summary"] or "500000" in result["executive_summary"]


def test_generate_moderate_probability_duration_scenario():
    """Test MODERATE probability + duration constraint scenario."""
    features = {
        "budget": 200000,
        "co2_reduction": 150,
        "social_impact": 6,
        "duration_months": 36,
    }
    shap_values = [
        {"feature": "duration_months", "shap_value": -0.15},
        {"feature": "budget", "shap_value": -0.05},
    ]

    result = generate_smart_explanation(0.45, features, shap_values)

    assert result["scenario"]["probability_level"] == "MODERATE"
    assert result["scenario"]["top_negative_factor"] == "duration_months"
    assert "36" in result["executive_summary"]
    assert len(result["risks"]) >= 2


def test_generate_high_probability_co2_scenario():
    """Test HIGH probability + strong CO2 performance."""
    features = {
        "budget": 300000,
        "co2_reduction": 5000,
        "social_impact": 8,
        "duration_months": 18,
    }
    shap_values = [
        {"feature": "co2_reduction", "shap_value": 0.25},
        {"feature": "budget", "shap_value": -0.02},  # Minor negative
    ]

    result = generate_smart_explanation(0.75, features, shap_values)

    assert result["scenario"]["probability_level"] == "HIGH"
    # When no strong negative, defaults to "default" or picks minor one
    assert "executive_summary" in result
    assert "recommendation" in result


def test_generate_with_gdp_factor():
    """Test scenario with country GDP as negative factor."""
    features = {
        "budget": 100000,
        "co2_reduction": 200,
        "social_impact": 7,
        "duration_months": 12,
        "country_gdp_per_capita": 3500,
    }
    shap_values = [
        {"feature": "country_gdp_per_capita", "shap_value": -0.18},
        {"feature": "budget", "shap_value": -0.05},
    ]

    result = generate_smart_explanation(0.35, features, shap_values)

    assert result["scenario"]["top_negative_factor"] == "country_gdp_per_capita"
    assert "3,500" in result["executive_summary"] or "3500" in result["executive_summary"]


def test_all_probability_levels_covered():
    """Test that all probability levels generate valid responses."""
    features = {
        "budget": 100000,
        "co2_reduction": 100,
        "social_impact": 5,
        "duration_months": 12,
    }

    for prob in [0.15, 0.45, 0.75]:
        result = generate_smart_explanation(prob, features, None)
        assert "executive_summary" in result
        assert "recommendation" in result
        assert "risks" in result
        assert len(result["executive_summary"]) > 50
        assert len(result["recommendation"]) > 50
        assert len(result["risks"]) >= 2


def test_feature_mapping():
    """Test that derived features map to base factors."""
    shap_values = [
        {"feature": "budget_per_month", "shap_value": -0.15},  # Maps to budget
        {"feature": "co2_per_dollar", "shap_value": -0.10},    # Maps to efficiency_score
    ]

    factor1 = get_top_negative_factor([shap_values[0]])
    assert factor1 == "budget"

    factor2 = get_top_negative_factor([shap_values[1]])
    assert factor2 == "efficiency_score"


def test_scenario_template_keys():
    """Test that scenario template keys are correctly formed."""
    features = {"budget": 100000, "co2_reduction": 100, "social_impact": 5, "duration_months": 12}

    scenarios = [
        (0.20, [{"feature": "budget", "shap_value": -0.2}], "LOW_budget"),
        (0.45, [{"feature": "duration_months", "shap_value": -0.15}], "MODERATE_duration_months"),
        (0.70, [{"feature": "co2_reduction", "shap_value": 0.2}], "HIGH_default"),
    ]

    for prob, shap, expected_key in scenarios:
        result = generate_smart_explanation(prob, features, shap)
        assert result["scenario"]["template_key"] == expected_key
