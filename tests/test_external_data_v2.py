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
    def test_indicators_collected(self):
        """The exact set collected, named rather than counted.

        This was `len(INDICATORS) == 6`: it failed on any change without saying
        which, and a count cannot notice the failure that mattered -- a
        consumer asking for a code nothing collects, which left the gdp_growth
        regressor at zero in every forecast (#86). What binds the two lists
        together now is tests/test_gdp_growth_regressor.py.
        """
        assert set(INDICATORS.values()) == {
            "EG.FEC.RNEW.ZS",     # renewable_share
            "SP.DYN.LE00.IN",     # life_expectancy
            "NY.GDP.PCAP.CD",     # gdp_per_capita (a level, in dollars)
            "SI.POV.GINI",        # gini_index
            "NY.GDP.MKTP.KD.ZG",  # gdp_growth (annual %) -- forecasting
        }

    def test_no_indicator_claims_a_source_that_refuses_it(self):
        """EN.ATM.CO2E.PC and GE.EST are not published by the World Bank.

        Both returned "The indicator was not found. It may have been deleted
        or archived" for every country, so every value ever stored under them
        came from the static benchmark fallback -- 15,316 rows each, none
        dated, wearing a World Bank code they never came from (#97).
        """
        from app.external_data import BENCHMARK_ONLY_INDICATORS

        assert "EN.ATM.CO2E.PC" not in INDICATORS.values()
        assert "GE.EST" not in INDICATORS.values()

        # Still offered by the platform, still available to the API -- just no
        # longer attributed to a source that does not publish them.
        assert set(BENCHMARK_ONLY_INDICATORS) == {
            "co2_per_capita", "gov_effectiveness"}
        for identifier in BENCHMARK_ONLY_INDICATORS.values():
            assert identifier.startswith("benchmark:"), (
                f"{identifier!r} still looks like a source's own code"
            )

    def test_new_indicators_present(self):
        from app.external_data import BENCHMARK_ONLY_INDICATORS

        # gov_effectiveness moved to the benchmark-only map (#97): the
        # platform still offers it, the World Bank never published it.
        offered = {**INDICATORS, **BENCHMARK_ONLY_INDICATORS}
        for key in ["gdp_per_capita", "gini_index", "gov_effectiveness"]:
            assert key in offered

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
        assert isinstance(val, (int, float)) and val > 1000

    @patch("app.external_data._fetch_indicator", return_value=None)
    @patch("app.external_data._fetch_oecd", return_value=55.55)
    def test_oecd_fallback(self, mock_oecd, mock_wb):
        # OECD stats.oecd.org deprecated since 2024, fallback goes to static benchmarks
        val = _fetch_with_fallback("DEU", "gdp_per_capita", "NY.GDP.PCAP.CD", "Germany")
        assert isinstance(val, (int, float)) and val > 1000

    @patch("app.external_data._fetch_indicator", return_value=None)
    @patch("app.external_data._fetch_oecd", return_value=None)
    def test_all_fail_unknown_country(self, mock_oecd, mock_wb, monkeypatch):
        monkeypatch.delenv("SORA_OFFLINE", raising=False)
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
        assert data is not None
        assert data["source"].startswith("World Bank API")
        assert "co2_per_capita" in data
        invalidate_cache()
    def test_iso3_new_countries(self):
        assert COUNTRY_ISO3["Indonesia"] == "IDN"
        assert COUNTRY_ISO3["Saudi Arabia"] == "SAU"
        assert COUNTRY_ISO3["Turkey"] == "TUR"


class TestBenchmarkOnlyIndicators:
    """Retired codes must not keep reaching the source that refuses them."""

    def test_they_never_touch_the_network(self, monkeypatch):
        """The point of retiring them, and it was nearly missed.

        Routing them through the normal chain looked harmless -- the World
        Bank refuses an internal identifier and BENCHMARKS supplies the value
        anyway -- but the request would still go out on every cache miss.
        Removing the codes was meant to stop exactly that.
        """
        import app.external_data as ed

        wb, oecd = [], []
        monkeypatch.setattr(ed, "_fetch_wb_indicator_dated",
                            lambda i, c, **k: wb.append(c) or (None, None))
        monkeypatch.setattr(ed, "_fetch_oecd_indicator",
                            lambda i, k: oecd.append(k) or None)
        ed.invalidate_cache()

        ed.get_country_esg_realtime("Germany")

        assert not [c for c in wb if str(c).startswith("benchmark:")], (
            f"a retired indicator was still requested from the World Bank: {wb}"
        )
        assert not [k for k in oecd if k in ed.BENCHMARK_ONLY_INDICATORS], (
            f"a retired indicator was still requested from OECD: {oecd}"
        )

    def test_they_are_still_served_to_the_api(self, monkeypatch):
        """Withdrawn from a source, not from the platform.

        Both keys are consumed by app/api/evaluate.py, app/api/map_data.py and
        the frontend's types; losing them would break those.
        """
        import app.external_data as ed

        monkeypatch.setattr(ed, "_fetch_wb_indicator_dated",
                            lambda i, c, **k: (None, None))
        ed.invalidate_cache()

        result = ed.get_country_esg_realtime("Germany")

        for key in ed.BENCHMARK_ONLY_INDICATORS:
            assert result["indicators"].get(key) is not None, f"{key} lost"
            assert result["indicator_sources"][key] == "benchmark", (
                f"{key} claims source {result['indicator_sources'][key]!r}"
            )
            assert result["indicator_periods"][key] is None, (
                f"{key} carries a period the benchmark never stated"
            )
