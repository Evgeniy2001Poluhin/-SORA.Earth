from fastapi.testclient import TestClient
from app.main import app
from app.auth import require_api_key

client = TestClient(app)

def _mock_api_key():
    return "test-api-key"

class TestDataPipeline:
    def setup_method(self):
        app.dependency_overrides[require_api_key] = _mock_api_key

    def teardown_method(self):
        app.dependency_overrides.pop(require_api_key, None)

    def test_data_status(self):
        r = client.get("/data/status")
        assert r.status_code == 200

    def test_countries_list(self):
        r = client.get("/data/countries")
        assert r.status_code == 200
        assert "count" in r.json()

    def test_country_germany(self):
        r = client.get("/data/country/Germany")
        assert r.status_code == 200
        assert r.json()["country"] == "Germany"

    def test_country_not_found(self):
        r = client.get("/data/country/Atlantis")
        assert r.status_code == 200

    def test_supported_countries(self):
        r = client.get("/data/countries/supported")
        assert r.status_code == 200

    def test_refresh_status(self):
        r = client.get("/data/refresh-status")
        assert r.status_code == 200
