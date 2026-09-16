"""A network without its weights answers nothing, and says so (#320).

`app/main.py` constructed `SoraNet()` unconditionally and loaded weights only
`if os.path.exists(NN_PATH)`. `models/pytorch_mlp.pth` is in neither Git nor
the image, so everywhere — CI, a fresh clone, and whatever production has not
been placed on its host by hand — the network kept torch's random
initialisation. `torch.manual_seed` is called nowhere, so each gunicorn worker
holds a *different* random network.

That output was then served as a prediction on `/predict/neural` and averaged in
as one third of the probability on `/predict/stacking`, `/predict/compare` and
`/report/pdf` — the last one rendered into a PDF the interface downloads. Every
indicator of it tested `nn_model is not None`, which a constructed object always
satisfies.

The contract, decided as "the network is optional":

* `nn_model` is `None` unless weights were actually loaded;
* `/predict/neural` answers 503 with a stable body and no probability — checked
  before the cache, so the refusal cannot depend on what the cache holds;
* the blended routes average the models that are loaded and list exactly those
  in `base_models`, omitting rather than nulling the absent one, as #316 does;
* every indicator reports the network's real state, and readiness does not
  depend on it.

**How the two states are produced, and why it matters.** The absent state is
the app's real import-time state: the weights file is not there, and the module
fixture below asserts that rather than assuming it. It is deliberately *not*
produced by setting `nn_model = None` — before the fix that raises inside
`_nn_forward` and answers 500, so "not 200" would pass for the wrong reason and
the test could never have been red. The loaded state is injected: a network
loaded from a real `state_dict` file, which takes the success path both before
and after the fix. Those controls are what make the red tests evidence.
"""
import math
import os

import pytest
import torch
from fastapi.testclient import TestClient

import app.api.predict as predict_module
import app.main as main
from app.api.infra import admin_auth
from app.main import app

client = TestClient(app)

PROJECT = {
    "name": "Neural Contract Solar",
    "budget": 420000,
    "co2_reduction": 900,
    "social_impact": 6.5,
    "duration_months": 20,
    "category": "Solar Energy",
    "region": "Europe",
    "lat": 48.0,
    "lon": 11.0,
}

SECOND = {**PROJECT, "name": "Neural Contract Wind", "budget": 260000, "co2_reduction": 610}


@pytest.fixture(scope="module", autouse=True)
def the_weights_are_really_absent():
    assert not os.path.exists(main.NN_PATH), (
        "%s exists. This module exercises the app's real import-time state and "
        "assumes the weights are not shipped; if they now are, produce the "
        "absent state through the loader instead of relying on the filesystem."
        % main.NN_PATH
    )


@pytest.fixture
def admin():
    app.dependency_overrides[admin_auth] = lambda: None
    yield
    app.dependency_overrides.pop(admin_auth, None)


def _network_loaded_from_file(directory):
    path = directory / "pytorch_mlp.pth"
    torch.save(main.SoraNet().state_dict(), path)
    network = main.SoraNet()
    network.load_state_dict(torch.load(path, map_location="cpu"))
    network.eval()
    return network


@pytest.fixture
def loaded_network(tmp_path, monkeypatch):
    """A network whose weights really came from a file — the success path."""
    network = _network_loaded_from_file(tmp_path)
    monkeypatch.setattr(main, "nn_model", network)
    return network


@pytest.fixture
def forward_calls(monkeypatch):
    """Records every call into the network without changing what it returns."""
    calls = []
    original = predict_module._nn_forward

    def spy(network, features):
        calls.append(network)
        return original(network, features)

    monkeypatch.setattr(predict_module, "_nn_forward", spy)
    return calls


def _mean(values):
    return sum(values) / len(values)


# ------------------------------------------------------------ absent: red today


def test_the_neural_route_refuses_without_weights():
    r = client.post("/api/v1/predict/neural", json=PROJECT)
    assert r.status_code == 503, "served %s with %r" % (r.status_code, r.json())
    body = r.json()
    assert body["reason_code"] == "neural_network_unavailable"
    assert "probability" not in body and "prediction" not in body


def test_a_cached_prediction_cannot_slip_past_the_refusal(monkeypatch):
    canned = {"prediction": 1, "probability": 99.9, "model": "NeuralNet"}
    monkeypatch.setattr(predict_module, "cache_get", lambda key: dict(canned))
    r = client.post("/api/v1/predict/neural", json=PROJECT)
    assert r.status_code == 503, "a cached answer was served: %r" % r.json()


def test_stacking_does_not_average_in_an_absent_network():
    r = client.post("/api/v1/predict/stacking", json=PROJECT)
    assert r.status_code == 200
    body = r.json()
    assert set(body["base_models"]) == {"rf", "xgb"}, body["base_models"]
    expected = _mean(list(body["base_models"].values()))
    assert math.isclose(body["probability"], expected, abs_tol=0.02), body


def test_compare_does_not_report_an_absent_network():
    r = client.post("/api/v1/predict/compare", json={"projects": [PROJECT, SECOND]})
    assert r.status_code == 200
    body = r.json()
    for project in body["projects"]:
        assert set(project["base_models"]) == {"rf", "xgb"}, project
    assert "NeuralNet" not in body


def test_the_pdf_report_does_not_consult_an_absent_network(forward_calls):
    r = client.post("/api/v1/report/pdf", json=PROJECT)
    assert r.status_code == 200
    assert forward_calls == [], "the report averaged in %d network call(s)" % len(forward_calls)


def test_model_health_says_the_network_is_not_loaded(admin):
    r = client.get("/api/v1/analytics/metrics/model-health")
    assert r.status_code == 200, r.text
    assert r.json()["models"]["pytorch_mlp"]["loaded"] is False


def test_model_health_status_reflects_the_champion_load_state(admin, monkeypatch):
    """`status` is derived, not a literal.

    The endpoint is named `model-health` and reports each model's `loaded` flag,
    yet it used to return `"status": "healthy"` unconditionally -- so a champion
    that failed to load looked exactly like one that answered. Both states in
    one test, so a field that always says "healthy" cannot pass (the #324 class,
    the same defect fixed for /health and /system/health).
    """
    import app.main as main

    healthy = client.get("/api/v1/analytics/metrics/model-health").json()
    assert healthy["status"] == "healthy", healthy

    monkeypatch.setattr(main, "rf_model", None)
    degraded = client.get("/api/v1/analytics/metrics/model-health").json()
    assert degraded["status"] == "degraded", (
        "status stayed 'healthy' with the RandomForest champion unloaded"
    )
    assert degraded["models"]["random_forest"]["loaded"] is False


def test_the_summary_says_the_network_is_not_loaded(admin):
    r = client.get("/api/v1/analytics/summary")
    assert r.status_code == 200, r.text
    assert r.json()["models_loaded"]["pytorch_mlp"] is False


def test_health_reports_the_real_network_state(tmp_path, monkeypatch):
    """Both states in one test, so a field that always says False cannot pass."""
    absent = client.get("/api/v1/health").json()["checks"]["models"]
    assert absent.get("neural_network_loaded") is False, absent

    monkeypatch.setattr(main, "nn_model", _network_loaded_from_file(tmp_path))
    present = client.get("/api/v1/health").json()["checks"]["models"]
    assert present.get("neural_network_loaded") is True, present


# ----------------------------------------- guards: green today, red if half-done


def test_readiness_does_not_depend_on_the_optional_network():
    """Green before the fix and after a correct one.

    Red only for the incomplete fix this file most expects: `nn_model` made
    honest while `_check_models` and `/system/health` still require it — which
    would take readiness down in every environment without the weights.

    Only `checks.models` is read, not the overall `/api/v1/health` status: that
    also folds in the database and external-data checks, and whether those pass
    depends on where the suite runs.
    """
    r = client.get("/api/v1/ready")
    assert r.status_code == 200, "an optional component took readiness down: %r" % r.json()
    models = client.get("/api/v1/health").json()["checks"]["models"]
    assert models["status"] == "healthy", models
    components = client.get("/system/health").json()["components"]
    assert components["ml_models"] == "ok", components


def test_the_readiness_score_ignores_the_optional_network(admin, tmp_path, monkeypatch):
    """The real absent state first, then the same call with weights injected.

    Equal before the fix, because an unloaded network still counted as loaded.
    Equal after a correct fix. Unequal — by the 30 points the aggregate awards —
    only if the network became None while the aggregate kept requiring it.
    """
    without = client.get("/api/v1/analytics/summary").json()["readiness_score"]
    monkeypatch.setattr(main, "nn_model", _network_loaded_from_file(tmp_path))
    with_network = client.get("/api/v1/analytics/summary").json()["readiness_score"]
    assert with_network == without, "the network moved readiness from %s to %s" % (without, with_network)


# ------------------------------------------------ controls: green before and after


def test_with_weights_the_neural_route_answers(loaded_network):
    r = client.post("/api/v1/predict/neural", json={**PROJECT, "name": "control neural"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "NeuralNet"
    assert 0.0 <= body["probability"] <= 100.0


def test_with_weights_stacking_averages_all_three(loaded_network):
    r = client.post("/api/v1/predict/stacking", json={**PROJECT, "name": "control stacking"})
    assert r.status_code == 200
    body = r.json()
    assert set(body["base_models"]) == {"rf", "xgb", "nn"}, body["base_models"]
    expected = _mean(list(body["base_models"].values()))
    assert math.isclose(body["probability"], expected, abs_tol=0.02), body


def test_with_weights_the_pdf_report_consults_the_network(loaded_network, forward_calls):
    r = client.post("/api/v1/report/pdf", json=PROJECT)
    assert r.status_code == 200
    assert forward_calls == [loaded_network]


def test_with_weights_model_health_says_loaded(admin, loaded_network):
    r = client.get("/api/v1/analytics/metrics/model-health")
    assert r.json()["models"]["pytorch_mlp"]["loaded"] is True


# ----------------------------------------------------------------- measurement


def test_unloaded_networks_disagree_and_loaded_ones_agree(tmp_path):
    """Why the absent state was never one wrong answer but several.

    Two networks constructed without weights give different outputs for the
    same input — which is what four gunicorn workers each did. Two loaded from
    the same file agree. Nothing here gates the fix; it pins the claim in #320.
    """
    # Small inputs on purpose. Raw features (a budget of 420000) drive the final
    # sigmoid into saturation, where two different random networks both print
    # exactly 0.0 — the first version of this test passed or failed by chance.
    features = torch.tensor([[0.3, -0.2, 0.5, 0.1, -0.4, 0.2, 0.0, 0.1, -0.1]])

    def output(network):
        network.eval()
        with torch.no_grad():
            return float(network(features)[0][0])

    first_worker, second_worker = main.SoraNet(), main.SoraNet()
    assert not torch.equal(first_worker.net[0].weight, second_worker.net[0].weight)
    assert output(first_worker) != output(second_worker)

    path = tmp_path / "shared.pth"
    torch.save(main.SoraNet().state_dict(), path)
    first, second = main.SoraNet(), main.SoraNet()
    first.load_state_dict(torch.load(path, map_location="cpu"))
    second.load_state_dict(torch.load(path, map_location="cpu"))
    assert output(first) == output(second)
