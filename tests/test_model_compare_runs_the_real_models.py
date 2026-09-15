"""POST /analytics/model-compare returns real model predictions (#323).

It used to compute four probabilities from hand-written linear formulas and
label them RandomForest / XGBoost / NeuralNet / StackingEnsemble -- numbers that
never touched a model, served to the compare panel. It now runs the same models
the prediction routes run, so the "RandomForest" probability is the one
`rf_model` actually produces, and the neural network appears only when loaded.
"""
URL = "/api/v1/analytics/model-compare"
PAYLOAD = {"budget": 150000, "co2_reduction": 60, "social_impact": 7, "duration_months": 12}


def _real_probs():
    import app.main as m
    from app.api.predict import _base_probabilities
    from app.validators import ProjectInput as Legacy

    legacy = Legacy(budget=PAYLOAD["budget"], co2_reduction=PAYLOAD["co2_reduction"],
                    social_impact=PAYLOAD["social_impact"], duration_months=PAYLOAD["duration_months"])
    feats_9 = m.make_features_base(legacy)
    feats_7 = m.make_features_xgb(legacy)
    return _base_probabilities(m.rf_model, m.xgb_model, m.nn_model, feats_9, feats_7)


def test_the_random_forest_probability_is_the_models_own(client):
    real = _real_probs()
    body = client.post(URL, json=PAYLOAD).json()

    assert body["models"]["RandomForest"]["probability"] == round(real["rf"] * 100, 2), (
        "the RandomForest number is not what rf_model produced"
    )
    assert body["models"]["XGBoost"]["probability"] == round(real["xgb"] * 100, 2), (
        "the XGBoost number is not what xgb_model produced"
    )


def test_the_neural_network_appears_only_when_loaded(client):
    import app.main as m

    body = client.post(URL, json=PAYLOAD).json()
    if m.nn_model is None:
        assert "NeuralNet" not in body["models"], (
            "NeuralNet was reported although its weights are not loaded (#320)"
        )
    else:
        assert "NeuralNet" in body["models"]


def test_the_shape_and_ensemble_are_preserved(client):
    body = client.post(URL, json=PAYLOAD).json()
    assert set(body) == {"models", "best_model", "threshold"}
    assert "RandomForest" in body["models"] and "XGBoost" in body["models"]
    assert "StackingEnsemble" in body["models"]
    for entry in body["models"].values():
        assert set(entry) == {"probability", "prediction"}
    assert body["best_model"] in body["models"]
