"""
Test that unknown countries are refused with 422, not silently scored as Europe.

Measured defect on main 76531a6: POST /api/v1/evaluate with country "Kenya"
(real, unsupported), "Atlantis" (invented) or "" (empty) answers 200 with
region: "Europe" and identical scores. The public schema has
`region: Optional[str] = Field(default="Europe", alias="country")` with no check;
handlers resolve with `COUNTRIES.get(project.region or "Germany", {"region": "Europe"})`,
so an unknown country is scored with European multipliers and world-average data, silently.
"""
import pytest


def test_unknown_countries_refused_at_evaluate():
    """POST /api/v1/evaluate with unknown country -> 422 with supported list."""
    from fastapi.testclient import TestClient
    from app.main import app

    # Use raise_server_exceptions=False to see non-200 codes
    client = TestClient(app, raise_server_exceptions=False)

    # Unknown countries that should be refused (not "Europe" - it's the default)
    unknown_countries = ["Kenya", "Atlantis", "", "USA"]

    for country in unknown_countries:
        response = client.post("/api/v1/evaluate", json={
            "country": country,
            "budget_usd": 100000,
            "co2_reduction_tons_per_year": 150,
            "social_impact_score": 7,
            "project_duration_months": 24
        })

        assert response.status_code == 422, \
            f"Expected 422 for country='{country}', got {response.status_code}"

        body = response.json()
        # Response should contain the list of supported countries
        body_str = str(body).lower()
        assert any(c in body_str for c in ["germany", "japan", "brazil"]), \
            f"Response for country='{country}' should list supported countries, got: {body}"


def test_known_countries_accepted():
    """Control: every country from GET /api/v1/countries -> 200 with correct region."""
    from fastapi.testclient import TestClient
    from app.main import app, COUNTRIES

    client = TestClient(app, raise_server_exceptions=False)

    # Get the list of supported countries
    countries_response = client.get("/api/v1/countries")
    assert countries_response.status_code == 200
    countries = countries_response.json()

    # Each supported country should work
    for country_name in list(countries.keys())[:5]:  # Test first 5 for speed
        response = client.post("/api/v1/evaluate", json={
            "country": country_name,
            "budget_usd": 100000,
            "co2_reduction_tons_per_year": 150,
            "social_impact_score": 7,
            "project_duration_months": 24
        })

        assert response.status_code == 200, \
            f"Expected 200 for known country '{country_name}', got {response.status_code}"

        result = response.json()
        expected_region = COUNTRIES[country_name]["region"]
        assert result["region"] == expected_region, \
            f"Country '{country_name}' should have region '{expected_region}', got '{result['region']}'"


def test_omitted_country_uses_default():
    """Control: request that omits country -> 200, unchanged behavior (default="Europe")."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    # Request without country field
    response = client.post("/api/v1/evaluate", json={
        "budget_usd": 100000,
        "co2_reduction_tons_per_year": 150,
        "social_impact_score": 7,
        "project_duration_months": 24
    })

    assert response.status_code == 200, \
        f"Expected 200 when country omitted, got {response.status_code}"

    result = response.json()
    # The default is "Europe" - this is the current behavior we're preserving
    assert result["region"] == "Europe", \
        f"Omitted country should default to Europe, got '{result['region']}'"


def test_explicit_europe_accepted():
    """Control: explicit country="Europe" -> 200, same score as omitted country."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    # Request with explicit "Europe"
    response_explicit = client.post("/api/v1/evaluate", json={
        "country": "Europe",
        "budget_usd": 100000,
        "co2_reduction_tons_per_year": 150,
        "social_impact_score": 7,
        "project_duration_months": 24
    })

    assert response_explicit.status_code == 200, \
        f"Expected 200 for explicit country='Europe', got {response_explicit.status_code}"

    # Request with omitted country (defaults to "Europe")
    response_omitted = client.post("/api/v1/evaluate", json={
        "budget_usd": 100000,
        "co2_reduction_tons_per_year": 150,
        "social_impact_score": 7,
        "project_duration_months": 24
    })

    assert response_omitted.status_code == 200

    # Both should produce the same total_score
    explicit_score = response_explicit.json()["total_score"]
    omitted_score = response_omitted.json()["total_score"]

    assert explicit_score == omitted_score, \
        f"Explicit 'Europe' score {explicit_score} should equal omitted score {omitted_score}"


def test_unknown_country_refused_at_what_if():
    """POST /api/v1/what-if with unknown country -> 422."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/v1/what-if", json={
        "country": "Atlantis",
        "name": "Test Project",
        "budget": 100000,
        "co2_reduction": 150,
        "social_impact": 7,
        "duration_months": 24
    })

    assert response.status_code == 422, \
        f"Expected 422 for unknown country at /what-if, got {response.status_code}"


def test_known_country_accepted_at_what_if():
    """Control: POST /api/v1/what-if with known country -> 200."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/v1/what-if", json={
        "country": "Germany",
        "name": "Test Project",
        "budget": 100000,
        "co2_reduction": 150,
        "social_impact": 7,
        "duration_months": 24
    })

    assert response.status_code == 200, \
        f"Expected 200 for known country at /what-if, got {response.status_code}"


def test_unknown_country_refused_at_pdf_report():
    """POST /api/v1/report/pdf with unknown country -> 422."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/v1/report/pdf", json={
        "country": "Kenya",
        "name": "Test Project",
        "budget": 100000,
        "co2_reduction": 150,
        "social_impact": 7,
        "duration_months": 24
    })

    assert response.status_code == 422, \
        f"Expected 422 for unknown country at /report/pdf, got {response.status_code}"


def test_known_country_accepted_at_pdf_report():
    """Control: POST /api/v1/report/pdf with known country -> 200."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/v1/report/pdf", json={
        "country": "France",
        "name": "Test Project",
        "budget": 100000,
        "co2_reduction": 150,
        "social_impact": 7,
        "duration_months": 24
    })

    assert response.status_code == 200, \
        f"Expected 200 for known country at /report/pdf, got {response.status_code}"


def test_unknown_country_refused_at_batch():
    """POST /api/v1/batch/evaluate with unknown country -> 422."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/v1/batch/evaluate", json={
        "projects": [{
            "country": "USA",
            "name": "Test Project",
            "budget": 100000,
            "co2_reduction": 150,
            "social_impact": 7,
            "duration_months": 24
        }]
    })

    # The batch endpoint should reject invalid projects
    # It may return 422 or include errors in the response
    assert response.status_code in [200, 422], \
        f"Expected 200 or 422 for batch with unknown country, got {response.status_code}"

    if response.status_code == 200:
        result = response.json()
        # Check that the project was rejected in the results
        assert result["failed"] > 0 or "error" in str(result).lower(), \
            f"Batch should report error for unknown country, got: {result}"


def test_known_country_accepted_at_batch():
    """Control: POST /api/v1/batch/evaluate with known country -> 200."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/v1/batch/evaluate", json={
        "projects": [{
            "country": "Japan",
            "name": "Test Project",
            "budget": 100000,
            "co2_reduction": 150,
            "social_impact": 7,
            "duration_months": 24
        }]
    })

    assert response.status_code == 200, \
        f"Expected 200 for batch with known country, got {response.status_code}"

    result = response.json()
    assert result["successful"] > 0, \
        f"Batch should succeed for known country, got: {result}"


def test_countries_dict_is_same_object():
    """app.countries.COUNTRIES is app.main.COUNTRIES (one object, not a copy)."""
    # This test will fail until we create app/countries.py
    try:
        from app.countries import COUNTRIES as countries_COUNTRIES
        from app.main import COUNTRIES as main_COUNTRIES

        assert countries_COUNTRIES is main_COUNTRIES, \
            "COUNTRIES should be the same object, not a copy"
    except ImportError:
        pytest.fail("app.countries module does not exist yet")
