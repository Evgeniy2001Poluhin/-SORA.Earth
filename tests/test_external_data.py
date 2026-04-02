"""Tests for external_data module."""
import pytest
from app.external_data import (
    get_supported_countries, get_merged_country_data,
    get_all_countries_merged, get_refresh_status, COUNTRY_ISO3,
)


def test_supported_countries_count():
    countries = get_supported_countries()
    assert len(countries) >= 20


def test_supported_countries_includes_major():
    countries = get_supported_countries()
    for c in ["Germany", "United States", "China", "Brazil", "Japan"]:
        assert c in countries


def test_merged_germany():
    data = get_merged_country_data("Germany")
    assert data is not None
    assert "co2_per_capita" in data or "esg_rank" in data


def test_merged_unknown():
    data = get_merged_country_data("Atlantis")
    assert data is None


def test_all_countries_merged():
    data = get_all_countries_merged()
    assert len(data) >= 20
    assert "Germany" in data


def test_refresh_status_keys():
    status = get_refresh_status()
    assert "static_countries" in status
    assert "running" in status


def test_iso3_mapping():
    assert COUNTRY_ISO3["Germany"] == "DEU"
    assert COUNTRY_ISO3["United States"] == "USA"
