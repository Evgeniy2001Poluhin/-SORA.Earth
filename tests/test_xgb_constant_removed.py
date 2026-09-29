"""XGBoost constant probability defect (#FINDING-9).

XGBoost returns 93.73% for every project because it was trained on scaled
features but receives unscaled ones. The variant chosen: remove it from all
blends that serve probabilities (/report/pdf, /predict/stacking,
/predict/compare, /analytics/model-compare), as the neural network was removed
in #320.
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_xgb_returns_constant_for_different_projects():
    """XGBoost returns the same probability (93.73%) for vastly different projects.

    This defect reproduction stays in the suite to document the original issue,
    but after the fix XGBoost is no longer called from blend routes, so this
    test verifies the defect exists in the model itself, not in production paths.
    """
    from app.main import xgb_model, make_features_xgb
    from app.validators import ProjectInput

    # Three very different projects: minimal budget vs massive budget
    projects = [
        {"budget": 5000, "co2_reduction": 1, "social_impact": 1, "duration_months": 1},
        {"budget": 500000, "co2_reduction": 100, "social_impact": 7, "duration_months": 24},
        {"budget": 10000000, "co2_reduction": 2000, "social_impact": 10, "duration_months": 120},
    ]

    probabilities = []
    for p in projects:
        data = ProjectInput(**p)
        X = make_features_xgb(data)
        prob = float(xgb_model.predict_proba(X)[0][1])
        probabilities.append(prob)

    # All three return the same probability
    assert len(set(round(p, 4) for p in probabilities)) == 1, (
        f"XGBoost should return constant probability, got: {probabilities}"
    )
    # The constant is 93.73%
    assert abs(probabilities[0] - 0.9373) < 0.001


def test_stacking_blend_excludes_xgb():
    """POST /predict/stacking blends only RandomForest (and neural net if loaded).

    XGBoost is excluded because it returns a constant. The blend must not
    include 'xgb' in base_models.
    """
    response = client.post("/api/v1/predict/stacking", json={
        "budget": 100000,
        "co2_reduction": 50,
        "social_impact": 7,
        "duration_months": 24
    })
    assert response.status_code == 200
    data = response.json()

    # base_models must not contain 'xgb'
    assert "xgb" not in data.get("base_models", {}), (
        f"XGBoost should not be in blend, got base_models: {data.get('base_models')}"
    )
    # Should have 'rf' and optionally 'nn' if weights loaded
    assert "rf" in data.get("base_models", {})


def test_compare_excludes_xgb():
    """POST /predict/compare returns individual model predictions without XGBoost.

    The compare output used to show XGBoost. After the fix it is excluded because
    it returns a constant probability.
    """
    response = client.post("/api/v1/predict/compare", json={
        "projects": [
            {
                "name": "Kenya",
                "budget": 5000,
                "co2_reduction": 1,
                "social_impact": 1,
                "duration_months": 6
            },
            {
                "name": "Germany",
                "budget": 500000,
                "co2_reduction": 100,
                "social_impact": 7,
                "duration_months": 24
            }
        ]
    })
    assert response.status_code == 200
    data = response.json()

    # Each project's base_models must not contain 'xgb'
    for project in data.get("projects", []):
        base_models = project.get("base_models", {})
        assert "xgb" not in base_models, (
            f"XGBoost should not be in {project['name']} base_models: {base_models}"
        )


def test_model_compare_excludes_xgb():
    """POST /analytics/model-compare shows model comparison without XGBoost."""
    response = client.post("/api/v1/analytics/model-compare", json={
        "budget": 100000,
        "co2_reduction": 50,
        "social_impact": 7,
        "duration_months": 24
    })
    assert response.status_code == 200
    data = response.json()

    models = data.get("models", {})
    # XGBoost should not appear in the models dict
    assert "XGBoost" not in models, (
        f"XGBoost should not be in model comparison, got: {list(models.keys())}"
    )
    # Should have RandomForest
    assert "RandomForest" in models


def test_pdf_uses_same_blend_as_stacking():
    """POST /report/pdf uses _base_probabilities just like /predict/stacking.

    The PDF generation calls _blend(_base_probabilities(...)) (see
    app/api/evaluate.py line 491), so after the fix it excludes XGBoost the same
    way /predict/stacking does. We verify the blend composition by checking the
    code path, not by parsing PDF bytes.

    This test documents that PDF and stacking share the blend logic.
    """
    from app.api.evaluate import generate_pdf_report
    import inspect

    # Verify generate_pdf_report imports and uses _base_probabilities and _blend
    source = inspect.getsource(generate_pdf_report)
    assert "_base_probabilities" in source, (
        "PDF generation should use _base_probabilities"
    )
    assert "_blend" in source, (
        "PDF generation should use _blend"
    )
    # The imports prove it uses the same functions as /predict/stacking
    assert "from app.api.predict import _base_probabilities, _blend" in source


def test_rf_alone_varies_with_inputs():
    """Control: RandomForest probabilities vary with inputs.

    This test shows that RandomForest is not constant (unlike XGBoost), so a
    blend containing only RF will still be useful.
    """
    from app.main import rf_model, make_features_base
    from app.validators import ProjectInput

    projects = [
        {"budget": 5000, "co2_reduction": 1, "social_impact": 1, "duration_months": 1},
        {"budget": 500000, "co2_reduction": 100, "social_impact": 7, "duration_months": 24},
    ]

    probabilities = []
    for p in projects:
        data = ProjectInput(**p)
        X = make_features_base(data)
        prob = float(rf_model.predict_proba(X)[0][1])
        probabilities.append(prob)

    # RandomForest returns different probabilities
    assert len(set(round(p, 2) for p in probabilities)) > 1, (
        f"RandomForest should vary with inputs, got: {probabilities}"
    )
