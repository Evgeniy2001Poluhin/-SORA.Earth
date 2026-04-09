"""Tests for expanded external_data: TTL cache, fallback chain, new indicators."""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
from app.external_data import (
    get_country_esg_realtime, get_supported_countries,
    get_merged_country_data, get_refresh_status,
    invalidate_cache, _is_cache_valid, _fetch_with_fallback,
    _live_cache, _cache_timestamps, CACHE_TTL, INDICATORS,
    COUNTRY_ISO3,
)
from app.country_benchmarks import BENCHMARKS, GLOBAL_AVG


class TestExpandedIndicators:
    def test_indicators_count(self):
        assert len(INDICATORS) == 6

    def test_new_indicators_present(self):
        for key in ["gdp_per_capita", "gini_index", "gov_effectiveness"]:
            assert key in INDICATORS

    def test_benchmarks_have_new_fields(self):
        for country, data in BENCHMARKS.items():
            assert "gdp_per_capita" in data, f"{country} missing gdp_per_capita"
            assert "gini_index" in data, f"{country} missing gini_index"
            assert "gov_effectiveness" in data, f"{country} missing gov_effectiveness"

    def test_benchmarks_expanded_count(self):
        assert len(BENCHMARKS) >= 30

    def test_global_avg_has_new_fields(self):
        for key in ["gdp_per_capita", "gini_index", "gov_effectiveness"]:
            assert key in GLOBAL_AVG

    def test_gov_effectiveness_range(self):
        for country, data in BENCHMARKS.items():
            assert -2.5 <= data["gov_effectiveness"] <= 2.5, f"{country} out of range"


class TestTTLCache:
    def setup_method(self):
        invalidate_cache()

    def test_cache_empty_initially(self):
        assert not _is_cache_valid("Germany")

    def test_cache_valid_after_set(self):
        _live_cache["TestCountry"] = {"test": True}
        _cache_timestamps["TestCountry"] = datetime.now()
        assert _is_cache_valid("TestCountry")

    def test_cache_expired(self):
        _live_cache["TestCountry"] = {"test": True}
        _cache_timestamps["TestCountry"] = datetime.now() - timedelta(hours=25)
        assert not _is_cache_valid("TestCountry")

    def test_invalidate_single(self):
        _live_cache["A"] = {"x": 1}
        _cache_timestamps["A"] = datetime.now()
        _live_cache["B"] = {"x": 2}
        _cache_timestamps["B"] = datetime.now()
        invalidate_cache("A")
        assert "A" not in _live_cache
        assert "B" in _live_cache
        invalidate_cache()

    def test_invalidate_all(self):
        _live_cache["A"] = {"x": 1}
        _cache_timestamps["A"] = datetime.now()
        invalidate_cache()
        assert len(_live_cache) == 0
        assert len(_cache_timestamps) == 0

    def test_refresh_status_shows_expired(self):
        _live_cache["Old"] = {"x": 1}
        _cache_timestamps["Old"] = datetime.now() - timedelta(hours=25)
        status = get_refresh_status()
        assert status["cache_expired"] >= 1
        assert status["cache_ttl_hours"] == 24.0
        invalidate_cache()


class TestFallbackChain:
    def setup_method(self):
        invalidate_cache()

    @patch("app.external_data._fetch_indicator", return_value=None)
    @patch("app.external_data._fetch_oecd", return_value=None)
    def test_fallback_to_benchmarks(self, mock_oecd, mock_wb):
        val = _fetch_with_fallback("DEU", "co2_per_capita", "EN.ATM.CO2E.PC", "Germany")
        assert val == BENCHMARKS["Germany"]["co2_per_capita"]

    @patch("app.external_data._fetch_indicator", return_value=99.99)
    def test_wb_takes_priority(self, mock_wb):
        val = _fetch_with_fallback("DEU", "gdp_per_capita", "NY.GDP.PCAP.CD", "Germany")
        assert val == 99.99

    @patch("app.external_data._fetch_indicator", return_value=None)
    @patch("app.external_data._fetch_oecd", return_value=55.55)
    def test_oecd_fallback(self, mock_oecd, mock_wb):
        # OECD stats.oecd.org deprecated since 2024, fallback goes to static benchmarks
        val = _fetch_with_fallback("DEU", "gdp_per_capita", "NY.GDP.PCAP.CD", "Germany")
        assert val == 48718  # static benchmark for Germany

    @patch("app.external_data._fetch_indicator", return_value=None)
    @patch("app.external_data._fetch_oecd", return_value=None)
    def test_all_fail_unknown_country(self, mock_oecd, mock_wb):
        val = _fetch_with_fallback("XXX", "co2_per_capita", "EN.ATM.CO2E.PC", "Atlantis")
        assert val is None


class TestEdgeCases:
    def setup_method(self):
        invalidate_cache()

    def test_unknown_country(self):
        assert get_country_esg_realtime("Atlantis") is None

    def test_new_countries_in_supported(self):
        countries = get_supported_countries()
        for c in ["Denmark", "Argentina", "Indonesia", "Nigeria", "Austria"]:
            assert c in countries

    def test_merged_narnia(self):
        assert get_merged_country_data("Narnia") is None

    @patch("app.external_data._fetch_indicator", return_value=None)
    @patch("app.external_data._fetch_oecd", return_value=None)
    def test_realtime_fallback_all_benchmarks(self, mock_oecd, mock_wb):
        data = get_country_esg_realtime("Germany")
        assert data is not None
        assert data["source"] == "World Bank API"
        assert "co2_per_capita" in data
        invalidate_cache()

    def test_iso3_new_countries(self):
        assert COUNTRY_ISO3["Indonesia"] == "IDN"
        assert COUNTRY_ISO3["Saudi Arabia"] == "SAU"
        assert COUNTRY_ISO3["Turkey"] == "TUR"
