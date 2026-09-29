"""Test that POST /api/v1/predict/uncertainty returns votes_for correctly.

The votes_for field must equal the count of trees whose probability >= 0.5 for
the success class, computed directly from rf_model.estimators_.
"""
import pytest
import numpy as np
from fastapi.testclient import TestClient

from app.main import app, rf_model, make_features
from app.validators import ProjectInput as PI

client = TestClient(app)


def compute_votes_for_directly(project_dict: dict) -> int:
    """Compute votes_for the same way the route does, from rf_model.estimators_."""
    p = PI(
        budget=project_dict["budget"],
        co2_reduction=project_dict["co2_reduction"],
        social_impact=project_dict["social_impact"],
        duration_months=project_dict["duration_months"],
    )
    feats = make_features(p)
    tree_preds = np.array([
        t.predict_proba(feats.values if hasattr(feats, "values") else feats)[0][1]
        for t in rf_model.estimators_
    ])
    return int(np.sum(tree_preds >= 0.5))


# Several different projects to test across different prediction ranges
TEST_PROJECTS = [
    {"budget": 50000, "co2_reduction": 50, "social_impact": 5, "duration_months": 12, "name": "baseline"},
    {"budget": 200000, "co2_reduction": 150, "social_impact": 8, "duration_months": 24, "name": "high_success"},
    {"budget": 10000, "co2_reduction": 10, "social_impact": 2, "duration_months": 6, "name": "low_success"},
    {"budget": 100000, "co2_reduction": 100, "social_impact": 6, "duration_months": 18, "name": "medium"},
    {"budget": 500000, "co2_reduction": 300, "social_impact": 9, "duration_months": 36, "name": "very_high"},
]


@pytest.mark.parametrize("project", TEST_PROJECTS, ids=lambda p: p["name"])
def test_votes_for_matches_direct_computation(project):
    """votes_for equals the count computed directly from rf_model.estimators_."""
    resp = client.post("/api/v1/predict/uncertainty", json=project)
    assert resp.status_code == 200, resp.text

    data = resp.json()
    expected_votes = compute_votes_for_directly(project)

    assert data["tree_distribution"]["votes_for"] == expected_votes, (
        f"votes_for mismatch for {project['name']}: "
        f"API returned {data['tree_distribution']['votes_for']}, "
        f"direct computation got {expected_votes}"
    )


def test_votes_for_is_between_zero_and_n_trees():
    """votes_for must be in the valid range [0, n_trees]."""
    for project in TEST_PROJECTS:
        resp = client.post("/api/v1/predict/uncertainty", json=project)
        assert resp.status_code == 200, resp.text

        data = resp.json()
        votes_for = data["tree_distribution"]["votes_for"]
        n_trees = data["tree_distribution"]["n_trees"]

        assert 0 <= votes_for <= n_trees, (
            f"{project['name']}: votes_for={votes_for} outside [0, {n_trees}]"
        )


def test_existing_fields_are_still_present():
    """Adding votes_for must not remove any existing field."""
    project = {"budget": 100000, "co2_reduction": 100, "social_impact": 6, "duration_months": 18}
    resp = client.post("/api/v1/predict/uncertainty", json=project)
    assert resp.status_code == 200, resp.text

    data = resp.json()

    # Top-level keys
    assert set(data.keys()) == {
        "probability", "prediction", "tree_distribution",
        "confidence", "uncertainty", "reliability"
    }

    # tree_distribution must have all the old fields plus votes_for
    dist = data["tree_distribution"]
    assert set(dist.keys()) == {"std", "n_trees", "min", "max", "p5", "p95", "votes_for"}

    # All fields must be present and valid
    assert isinstance(dist["std"], (int, float))
    assert isinstance(dist["n_trees"], int)
    assert isinstance(dist["min"], (int, float))
    assert isinstance(dist["max"], (int, float))
    assert isinstance(dist["p5"], (int, float))
    assert isinstance(dist["p95"], (int, float))
    assert isinstance(dist["votes_for"], int)
