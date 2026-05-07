"""Pytest fixtures for regression suite (TestClient — no live server needed)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def client():
    return TestClient(app)
