"""Contract tests for POST /api/v1/predict/uncertainty.

The web client types this response in web/src/api/types.ts (UncertaintyResponse).
Those types drive what the UI reads, so a silent shape change here breaks
UncertaintyCard without any compile-time signal. These tests pin the contract.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

PROJECT = {
    "name": "Uncertainty Contract",
    "budget": 200000,
    "co2_reduction": 120,
    "social_impact": 7,
    "duration_months": 24,
    "region": "Germany",
}


@pytest.fixture(scope="module")
def payload():
    resp = client.post("/api/v1/predict/uncertainty", json=PROJECT)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_top_level_keys(payload):
    assert set(payload) == {
        "probability",
        "prediction",
        "tree_distribution",
        "confidence",
        "uncertainty",
        "reliability",
    }


def test_prediction_block(payload):
    prediction = payload["prediction"]
    assert set(prediction) == {"mean", "median", "lower_90", "upper_90"}
    for key, value in prediction.items():
        assert isinstance(value, float), key


def test_tree_distribution_carries_p5_and_p95(payload):
    """UncertaintyCard reads tree_distribution.p5 / .p95 directly."""
    dist = payload["tree_distribution"]
    assert set(dist) == {"std", "n_trees", "min", "max", "p5", "p95"}
    assert isinstance(dist["n_trees"], int)
    assert dist["p5"] <= dist["p95"]
    assert dist["min"] <= dist["p5"]
    assert dist["p95"] <= dist["max"]


def test_percentiles_match_prediction_bounds(payload):
    """p5/p95 are the same percentiles exposed as lower_90/upper_90."""
    dist = payload["tree_distribution"]
    prediction = payload["prediction"]
    assert dist["p5"] == prediction["lower_90"]
    assert dist["p95"] == prediction["upper_90"]


def test_uncertainty_block(payload):
    unc = payload["uncertainty"]
    assert set(unc) == {"method", "mean", "std", "ci_90", "n_trees"}
    assert isinstance(unc["ci_90"], list) and len(unc["ci_90"]) == 2
    assert unc["ci_90"][0] <= unc["ci_90"][1]


def test_confidence_and_reliability_are_the_same_enum(payload):
    allowed = {"high", "medium", "low"}
    assert payload["confidence"] in allowed
    assert payload["reliability"] in allowed
    assert payload["reliability"] == payload["confidence"]


def test_no_field_is_null(payload):
    """The endpoint has a single unconditional return, so nothing is optional."""

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")
        else:
            assert node is not None, path

    walk(payload)
