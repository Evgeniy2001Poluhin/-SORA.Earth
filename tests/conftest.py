import pytest
from fastapi.testclient import TestClient
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.main import app

@pytest.fixture(scope="module")
def client():
    return TestClient(app)

@pytest.fixture
def sample_project():
    return {"name": "Test", "budget": 50000, "co2_reduction": 70, "social_impact": 7, "duration_months": 12}

@pytest.fixture
def high_project():
    return {"name": "High", "budget": 100000, "co2_reduction": 100, "social_impact": 10, "duration_months": 24}

@pytest.fixture
def low_project():
    return {"name": "Low", "budget": 500, "co2_reduction": 2, "social_impact": 1, "duration_months": 3}

@pytest.fixture
def ghg_data():
    return {"electricity_kwh": 10000, "natural_gas_m3": 500, "diesel_liters": 200,
            "petrol_liters": 300, "flights_km": 5000, "waste_kg": 1000}
