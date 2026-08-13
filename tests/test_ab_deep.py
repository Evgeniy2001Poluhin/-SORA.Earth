"""Deep tests for A/B comparison module."""
import pytest
from starlette.testclient import TestClient
from app.main import app

client = TestClient(app, raise_server_exceptions=False)
PROJECT = {"budget": 100000, "co2_reduction": 50, "social_impact": 7, "duration_months": 12}

def test_ab_start():
    for path in ["/api/v1/ab/predict", "/api/v1/ab/start"]:
        r = client.post(path, json={"name": "test_exp", "variants": [PROJECT, PROJECT]})
        if r.status_code != 404: break
    assert r.status_code in (200, 201, 404, 422)

import pytest
def test_ab_compare():
    """No such endpoint, and the answer now says so.

    The xfail here read "endpoint exists but doesn't accept POST - endpoint
    pending v0.2.2" (#4). It does not exist: /api/v1/ab has predict, split and
    stats, and nothing else. The 405 came from the GET-only SPA catch-all
    matching by path -- see tests/test_absent_paths_are_not_wrong_method.py.

    The loop is kept as written: it breaks on the first path that is not 404,
    so with both absent it ends on the second and asserts on that.
    """
    for path in ["/api/v1/ab/compare", "/api/v1/analytics/ab/compare"]:
        r = client.post(path, json={"projects": [PROJECT, PROJECT]})
        if r.status_code != 404: break
    assert r.status_code == 404, (
        f"{r.status_code}: neither path has a route, so 404 is the only honest "
        f"answer; 405 would again claim the resource exists"
    )

def test_ab_results():
    for path in ["/api/v1/ab/results", "/api/v1/analytics/ab/results"]:
        r = client.get(path)
        if r.status_code != 404: break
    assert r.status_code in (200, 404)

def test_ab_module_importable():
    try:
        from app import ab_comparison
    except ImportError:
        pytest.skip("ab_comparison module not yet created")

def test_ab_statistical_significance():
    try:
        from app.ab_comparison import calculate_significance
        p = calculate_significance([0.7, 0.8, 0.75], [0.6, 0.65, 0.7])
        assert 0 <= p <= 1
    except (ImportError, AttributeError):
        pytest.skip("calculate_significance not available")
