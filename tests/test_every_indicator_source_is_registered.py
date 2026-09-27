"""Every label the country-data fallback chain can emit is in SOURCE_REGISTER.

The chain in app/external_data.py _fetch_with_fallback_impl returns labels
"world_bank", "oecd", "benchmark", "global_avg" or "none". get_country_esg_realtime
stores the label in result["indicator_sources"] only when the value is not None,
and also labels BENCHMARK_ONLY_INDICATORS as "benchmark".

Two labels reached API responses with no declared provenance: "benchmark" and
"global_avg". This test drives the chain to enumerate every label it can emit
(not by listing them by hand), then asserts every one except "none" is a
SOURCE_REGISTER key.
"""
import pytest

from app.external_data import get_country_esg_realtime, COUNTRY_ISO3, BENCHMARKS
from app.ingesters.source_register import SOURCE_REGISTER


def test_every_indicator_source_label_is_registered(monkeypatch):
    """Drive the fallback chain to emit every label, assert all are registered.

    The fallback order is: World Bank → OECD → benchmark → global_avg.
    We drive it by monkeypatching the fetchers to control what they return.
    """
    from app import external_data

    # Collect every label the chain emits across different scenarios
    emitted_labels = set()

    # Scenario 1: World Bank returns a value
    monkeypatch.setattr(external_data, "_fetch_wb_indicator_dated",
                        lambda *a, **k: (42.0, "2025"))
    monkeypatch.setattr(external_data, "_fetch_oecd_indicator",
                        lambda *a, **k: None)
    monkeypatch.setenv("SORA_OFFLINE", "0")
    external_data.invalidate_cache()

    result = get_country_esg_realtime("Germany")
    assert result is not None, "scenario 1 must return a profile"
    emitted_labels.update(result.get("indicator_sources", {}).values())

    # Scenario 2: World Bank returns None, OECD returns a value
    monkeypatch.setattr(external_data, "_fetch_wb_indicator_dated",
                        lambda *a, **k: (None, None))
    monkeypatch.setattr(external_data, "_fetch_oecd_indicator",
                        lambda iso3, key: 99.0 if key == "gdp_per_capita" else None)
    external_data.invalidate_cache()

    result = get_country_esg_realtime("Germany")
    assert result is not None, "scenario 2 must return a profile"
    emitted_labels.update(result.get("indicator_sources", {}).values())

    # Scenario 3: World Bank and OECD both None, falls back to benchmark
    # (Germany is in BENCHMARKS)
    monkeypatch.setattr(external_data, "_fetch_wb_indicator_dated",
                        lambda *a, **k: (None, None))
    monkeypatch.setattr(external_data, "_fetch_oecd_indicator",
                        lambda *a, **k: None)
    external_data.invalidate_cache()

    result = get_country_esg_realtime("Germany")
    assert result is not None, "scenario 3 must return a profile"
    emitted_labels.update(result.get("indicator_sources", {}).values())

    # Scenario 4: SORA_OFFLINE=1, falls back to global_avg when benchmark missing
    # Temporarily add a fake country to COUNTRY_ISO3 to force global_avg fallback
    monkeypatch.setenv("SORA_OFFLINE", "1")
    monkeypatch.setitem(external_data.COUNTRY_ISO3, "TestCountryNotInBenchmarks", "TST")
    external_data.invalidate_cache()

    result = get_country_esg_realtime("TestCountryNotInBenchmarks")
    assert result is not None, "scenario 4 must return a profile"
    emitted_labels.update(result.get("indicator_sources", {}).values())

    # Remove "none" from the set - it's explicitly allowed to be unregistered
    # because it means "no value was found"
    emitted_labels.discard("none")

    # Assert every emitted label (except "none") is in SOURCE_REGISTER
    for label in emitted_labels:
        assert label in SOURCE_REGISTER, (
            f"indicator source label '{label}' appears in API responses but is "
            f"not a SOURCE_REGISTER key. Available keys: {sorted(SOURCE_REGISTER.keys())}"
        )

    # Control: assert we actually drove all the paths we intended to
    # The fallback chain can emit: world_bank, oecd, benchmark, global_avg (plus "none")
    expected_labels = {"world_bank", "oecd", "benchmark", "global_avg"}
    assert emitted_labels == expected_labels, (
        f"test setup error: expected to collect {expected_labels}, "
        f"got {emitted_labels}. Missing: {expected_labels - emitted_labels}"
    )


def test_none_is_never_stored_in_indicator_sources(monkeypatch):
    """The 'none' label means no value was found and must not appear in responses.

    get_country_esg_realtime stores the label in indicator_sources only when
    val is not None. This test verifies that behavior by making most indicators
    resolve to None while one resolves to a value, then asserting "none" never
    appears in the stored sources.
    """
    from app import external_data

    # World Bank stub: return a value for ONE indicator, None for others
    def wb_stub(iso3, indicator_code):
        if indicator_code == "NY.GDP.PCAP.CD":  # gdp_per_capita
            return (12345.0, "2023")
        return (None, None)

    monkeypatch.setattr(external_data, "_fetch_wb_indicator_dated", wb_stub)
    monkeypatch.setattr(external_data, "_fetch_oecd_indicator",
                        lambda *a, **k: None)
    monkeypatch.setenv("SORA_OFFLINE", "0")

    # Temporarily add a fake country to COUNTRY_ISO3, not in BENCHMARKS,
    # so benchmark fallback gives None for indicators WB didn't resolve
    monkeypatch.setitem(external_data.COUNTRY_ISO3, "TestCountryNotInBenchmarks", "TST")
    external_data.invalidate_cache()
    result = get_country_esg_realtime("TestCountryNotInBenchmarks")

    assert result is not None, "profile must exist when at least one indicator resolves"
    sources = result.get("indicator_sources", {})
    assert len(sources) > 0, "indicator_sources must be non-empty (gdp_per_capita resolved)"
    assert "none" not in sources.values(), (
        "the 'none' label appeared in indicator_sources, which means "
        "a None value was stored when it should have been skipped"
    )
