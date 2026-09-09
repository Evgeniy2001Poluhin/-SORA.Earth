"""Contract tests for POST /api/v1/predict/uncertainty.

The web client types this response in web/src/api/types.ts (UncertaintyResponse).
Those types drive what the UI reads, so a silent shape change here breaks
UncertaintyCard without any compile-time signal. These tests pinned the
contract before there was a `response_model` to check it: this file existed
first, and docs/API_CONTRACT_ROADMAP.md §4 P1 is what added the declaration
these tests were substituting for.
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


def test_the_live_body_validates_against_the_declared_model(payload):
    """The model is not aspirational: the real handler's own output must pass it."""
    from app.schemas import UncertaintyOk

    validated = UncertaintyOk.model_validate(payload)
    # Round-tripped through JSON mode, not just accepted: a model that
    # silently dropped or renamed a field would still "validate" an extra key
    # away under pydantic's default mode, and this catches that by comparing
    # shape. `mode="json"` rather than the bare dump: `ci_90` is declared
    # `Tuple[float, float]` so field content matches the two-element array
    # this endpoint has always sent, and the default dump keeps it a Python
    # tuple, which is not what either the wire format or `payload` (parsed
    # from real JSON) ever contains.
    assert validated.model_dump(mode="json") == payload


def test_the_route_declares_the_contract():
    """The migration itself, at the route table.

    Every test above describes the handler's output and stays green if
    `response_model` is deleted -- measured on the sibling migrations in this
    series, every time. The declaration is what reaches `/openapi.json` and
    what the ratchet counts; without this assertion the contract could be
    undone and nothing here would notice.
    """
    from app.schemas import UncertaintyOk

    route = next(
        r for r in app.routes
        if getattr(r, "path", "") == "/api/v1/predict/uncertainty"
    )

    assert route.response_model is UncertaintyOk, (
        f"POST /api/v1/predict/uncertainty declares "
        f"{route.response_model!r}, not UncertaintyOk"
    )


def test_removing_the_declaration_fails_the_ratchet():
    """The ratchet must be able to tell this contract apart from a missing one.

    Runs the actual inventory script -- `collect()`, the same function
    `scripts/api_contract_inventory.py --ratchet` calls -- against a copy of
    the route source with `response_model=UncertaintyOk` deleted, and checks
    that the route it finds is uncovered. Not trusted by reasoning about the
    script; this is the mutant that proves it, over the real function rather
    than a description of it.
    """
    import sys
    import importlib
    import tempfile
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo / "scripts"))
    try:
        inventory = importlib.import_module("api_contract_inventory")
    finally:
        sys.path.pop(0)

    source = (repo / "app" / "api" / "calibration.py").read_text()
    mutated = source.replace(
        '@router.post("/predict/uncertainty", response_model=UncertaintyOk)',
        '@router.post("/predict/uncertainty")',
        1,
    )
    assert mutated != source, "the anchor this test patches is gone from calibration.py"

    with tempfile.TemporaryDirectory() as tmp:
        # `collect()` walks a real app_dir with `rglob`, so the mutated source
        # has to exist on disk under an `app/api/` shape -- feeding it a
        # parsed AST directly is not this function's contract.
        app_dir = Path(tmp) / "app"
        (app_dir / "api").mkdir(parents=True)
        (app_dir / "api" / "calibration.py").write_text(mutated)

        routes = inventory.collect(app_dir)
        target = next(
            r for r in routes
            if "POST" in r.methods and r.decl_path == "/predict/uncertainty"
        )

        assert target.response_model is None, (
            "the inventory still sees a response_model after it was deleted "
            "from the source; this test's mutation and the real ratchet are "
            "not looking at the same thing"
        )
