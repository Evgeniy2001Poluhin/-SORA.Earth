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

def pytest_addoption(parser):
    parser.addoption('--integration', action='store_true', default=False, help='run integration tests')

def pytest_configure(config):
    config.addinivalue_line('markers', 'integration: mark test as integration (external API, multiprocessing)')

def pytest_collection_modifyitems(config, items):
    if config.getoption('--integration'):
        return
    skip_mark = pytest.mark.skip(reason='use --integration to run')
    for item in items:
        if 'integration' in item.keywords:
            item.add_marker(skip_mark)
