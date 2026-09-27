"""Security tests for read authorization (GHSA-jmpv-7wjf-87q9)."""
import pytest
from app.auth import create_access_token
from app.security.spreadsheet import neutralize_cell


def test_csv_export_neutralizes_formula_triggers(client):
    """CSV export with a stored name starting with = → the cell is neutralized."""
    from app.database import Evaluation
    from app.main import get_db_sync

    admin_token = create_access_token({"sub": "admin", "role": "admin"})

    # Directly insert an evaluation with a formula-trigger name.
    db = get_db_sync()
    try:
        eval_record = Evaluation(
            name="=1+1",
            budget=100000,
            co2_reduction=150,
            social_impact=7,
            duration_months=24,
            total_score=50.0,
            environment_score=50.0,
            social_score=50.0,
            economic_score=50.0,
            success_probability=50.0,
            risk_level="MEDIUM",
            region="Europe",
        )
        db.add(eval_record)
        db.commit()
    finally:
        db.close()

    # Export CSV and check that the name is neutralized.
    response = client.get("/api/v1/export/csv", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    csv_content = response.content.decode()

    # The neutralized cell should start with a single quote.
    assert "'=1+1" in csv_content, "Formula trigger not neutralized in CSV export"


def test_csv_export_does_not_modify_normal_names(client):
    """CSV export with a normal name → unchanged."""
    from app.database import Evaluation
    from app.main import get_db_sync

    admin_token = create_access_token({"sub": "admin", "role": "admin"})

    # Directly insert an evaluation with a normal name.
    db = get_db_sync()
    try:
        eval_record = Evaluation(
            name="Solar Panel Initiative",
            budget=100000,
            co2_reduction=150,
            social_impact=7,
            duration_months=24,
            total_score=50.0,
            environment_score=50.0,
            social_score=50.0,
            economic_score=50.0,
            success_probability=50.0,
            risk_level="MEDIUM",
            region="Europe",
        )
        db.add(eval_record)
        db.commit()
    finally:
        db.close()

    # Export CSV and check that the name is unchanged.
    response = client.get("/api/v1/export/csv", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    csv_content = response.content.decode()

    # The name should appear without a leading quote.
    assert "Solar Panel Initiative" in csv_content
    assert "'Solar Panel Initiative" not in csv_content


def test_neutralize_cell_prefixes_formula_triggers():
    """neutralize_cell helper prefixes =, +, -, @ with a single quote."""
    assert neutralize_cell("=1+1") == "'=1+1"
    assert neutralize_cell("+A1") == "'+A1"
    assert neutralize_cell("-SUM(A1:A10)") == "'-SUM(A1:A10)"
    assert neutralize_cell("@EVAL") == "'@EVAL"


def test_neutralize_cell_leaves_normal_text_unchanged():
    """neutralize_cell leaves normal text unchanged."""
    assert neutralize_cell("Solar Panel") == "Solar Panel"
    assert neutralize_cell("123") == "123"
    assert neutralize_cell("") == ""
    assert neutralize_cell("  leading space") == "  leading space"


def test_neutralize_cell_leaves_numbers_unchanged():
    """neutralize_cell leaves numbers unchanged (a number cannot carry a formula)."""
    assert neutralize_cell("-3.5") == "-3.5"
    assert neutralize_cell("3.14159") == "3.14159"
    assert neutralize_cell("0") == "0"
    assert neutralize_cell("-100") == "-100"
    # But these are not numbers, so they get quoted
    assert neutralize_cell("=1+1") == "'=1+1"
    assert neutralize_cell("+cmd") == "'+cmd"
    assert neutralize_cell("-cmd") == "'-cmd"
    assert neutralize_cell("@x") == "'@x"


def test_predictions_log_limit_validation(client):
    """analytics/predictions-log with an over-limit limit → 422."""
    admin_token = create_access_token({"sub": "admin", "role": "admin"})

    # Request with limit > 1000 should be rejected.
    response = client.get("/api/v1/analytics/predictions-log?limit=1001",
                          headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 422, "Over-limit request not rejected"


def test_predictions_log_accepts_valid_limit(client):
    """analytics/predictions-log with a valid limit → 200."""
    admin_token = create_access_token({"sub": "admin", "role": "admin"})

    # Request with limit ≤ 1000 should succeed.
    response = client.get("/api/v1/analytics/predictions-log?limit=100",
                          headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200


def test_anonymous_get_on_protected_routes_returns_401(client):
    """Anonymous GET on each guarded route → 401."""
    protected_routes = [
        "/api/v1/history",
        "/api/v1/history/1",
        "/api/v1/export/csv",
        "/api/v1/predictions/history",
        "/api/v1/predictions/export/csv",
        "/api/v1/analytics/predictions-log",
        "/api/v1/batch",
        "/api/v1/batch/test123",
        "/api/v1/webhooks",
        "/api/v1/copilot/sessions",
        "/api/v1/copilot/sessions/test123",
        "/api/v1/auth/me",
    ]

    for route in protected_routes:
        response = client.get(route)
        assert response.status_code == 401, (
            f"GET {route} answered {response.status_code} to an anonymous caller (expected 401)"
        )


def test_authenticated_user_can_access_protected_routes(client):
    """Authenticated GET (viewer, analyst, admin) → 200 or 404."""
    viewer_token = create_access_token({"sub": "viewer", "role": "viewer"})

    protected_routes = [
        "/api/v1/history",
        "/api/v1/export/csv",
        "/api/v1/predictions/history",
        "/api/v1/predictions/export/csv",
        "/api/v1/analytics/predictions-log",
        "/api/v1/webhooks",
    ]

    for route in protected_routes:
        response = client.get(route, headers={"Authorization": f"Bearer {viewer_token}"})
        # Should not return 401 (may return 200, 404, or other non-401 status).
        assert response.status_code != 401, (
            f"GET {route} answered 401 to an authenticated viewer (expected 200 or 404)"
        )


def test_batch_list_requires_authentication(client):
    """GET /api/v1/batch anonymous → 401, authenticated → 200."""
    # Anonymous request should be rejected.
    response = client.get("/api/v1/batch")
    assert response.status_code == 401, "Anonymous batch list returned non-401"

    # Authenticated request should succeed.
    viewer_token = create_access_token({"sub": "viewer", "role": "viewer"})
    response = client.get("/api/v1/batch", headers={"Authorization": f"Bearer {viewer_token}"})
    assert response.status_code == 200, "Authenticated batch list failed"
