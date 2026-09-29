"""
Country data in the ESG score names its source and year.

Before: app/country_benchmarks.py said "based on World Bank & UN data (expanded v2)"
with no year recorded, and research showed the values were a patchwork of different
years per country even within one indicator. The PDF report printed ESG scores without
saying where the country data came from.

After: CO2 per capita, GDP per capita, and HDI refreshed to one common year per
indicator for all countries from named World Bank and UNDP series. SOURCES dict
records provenance for every field. PDF report states the sources. Source register
entries updated to reflect what is now true.
"""
import base64
import re
import zlib
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient


# PDF text extraction functions from tests/test_reports_no_fake_ml.py
def _decoded_streams(data: bytes):
    for m in re.finditer(rb"stream\r?\n", data):
        start = m.end(); end = data.find(b"endstream", start)
        if end < 0:
            continue
        body = data[start:end].strip()
        try:
            if body.endswith(b"~>"):
                body = base64.a85decode(body[:-2])
            yield zlib.decompress(body)
        except Exception:
            continue

def pdf_text(data: bytes) -> str:
    parts = []
    for s in _decoded_streams(data):
        for raw in re.findall(rb"\(((?:\\.|[^\\)])*)\)\s*Tj", s):
            parts.append(raw.replace(rb"\(", b"(").replace(rb"\)", b")").replace(rb"\\\\", b"\\").decode("latin-1"))
    return " ".join(parts)


def test_sources_has_entry_for_every_benchmark_field():
    """SOURCES records provenance for every field of every benchmark record."""
    from app.country_benchmarks import BENCHMARKS, SOURCES

    # Get all fields from any benchmark record
    any_country = next(iter(BENCHMARKS.values()))
    benchmark_fields = set(any_country.keys())

    # SOURCES must have an entry for every field
    sources_fields = set(SOURCES.keys())
    assert sources_fields == benchmark_fields, \
        f"SOURCES fields {sources_fields} != benchmark fields {benchmark_fields}"

    # Every SOURCES entry must have at least: source, series, year, note
    required_keys = {"source", "series", "year", "note"}
    for field, src in SOURCES.items():
        missing = required_keys - set(src.keys())
        assert not missing, \
            f"SOURCES['{field}'] missing required keys: {missing}"


def test_refreshed_indicators_have_int_years():
    """co2_per_capita, gdp_per_capita, hdi have int year; renewable_share has year None."""
    from app.country_benchmarks import SOURCES

    # The three refreshed indicators must have an int year
    for field in ["co2_per_capita", "gdp_per_capita", "hdi"]:
        year = SOURCES[field]["year"]
        assert isinstance(year, int), \
            f"SOURCES['{field}']['year'] should be int, got {type(year).__name__}: {year}"
        assert year >= 2020, \
            f"SOURCES['{field}']['year'] should be recent, got {year}"

    # renewable_share was NOT refreshed, year should be None
    ren_year = SOURCES["renewable_share"]["year"]
    assert ren_year is None, \
        f"SOURCES['renewable_share']['year'] should be None (not refreshed), got {ren_year}"

    # ...and should have a note explaining why
    ren_note = SOURCES["renewable_share"]["note"]
    assert "not refreshed" in ren_note or "carried over" in ren_note, \
        f"SOURCES['renewable_share']['note'] should explain why not refreshed, got: {ren_note}"


def test_pdf_contains_source_line():
    """PDF report includes a provenance line stating sources and years."""
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    # Generate a PDF
    payload = {
        "name": "Test Project",
        "country": "Germany",
        "category": "Solar Energy",
        "budget_usd": 100000,
        "co2_reduction_tons_per_year": 150,
        "social_impact_score": 7,
        "project_duration_months": 24
    }
    response = client.post("/api/v1/report/pdf", json=payload)
    assert response.status_code == 200

    # Extract text from PDF
    text = pdf_text(response.content)

    # Must contain the source attribution line
    assert "Country data:" in text, \
        "PDF should contain 'Country data:' attribution line"
    assert "World Bank" in text, \
        "PDF should mention World Bank as source"
    assert "UNDP" in text or "HDI" in text, \
        "PDF should mention UNDP or HDI source"
    # Check years are present (as strings in the PDF)
    assert "2024" in text, \
        "PDF should mention 2024 (World Bank data year)"
    assert "2022" in text, \
        "PDF should mention 2022 (UNDP HDI year)"


def test_pdf_source_line_follows_sources_dict():
    """PDF source line is derived from SOURCES, not hard-coded."""
    from app.main import app
    from app.country_benchmarks import SOURCES

    client = TestClient(app, raise_server_exceptions=False)

    payload = {
        "name": "Test Project",
        "country": "France",
        "category": "Wind Energy",
        "budget_usd": 100000,
        "co2_reduction_tons_per_year": 150,
        "social_impact_score": 7,
        "project_duration_months": 24
    }

    # Generate PDF with original SOURCES
    response = client.post("/api/v1/report/pdf", json=payload)
    assert response.status_code == 200
    original_text = pdf_text(response.content)

    # Monkeypatch SOURCES to change CO2 year, GDP year, and renewable years
    fake_co2_year = 9999
    fake_gdp_year = 8888
    fake_ren_years = "1111-2222"

    with patch.dict("app.api.evaluate.SOURCES", {
        "co2_per_capita": {**SOURCES["co2_per_capita"], "year": fake_co2_year},
        "gdp_per_capita": {**SOURCES["gdp_per_capita"], "year": fake_gdp_year},
        "hdi": SOURCES["hdi"],
        "renewable_share": {**SOURCES["renewable_share"], "years": fake_ren_years},
        "esg_rank": SOURCES["esg_rank"],
        "gini_index": SOURCES["gini_index"],
        "gov_effectiveness": SOURCES["gov_effectiveness"],
    }):
        # Generate PDF with patched SOURCES
        response2 = client.post("/api/v1/report/pdf", json=payload)
        assert response2.status_code == 200
        patched_text = pdf_text(response2.content)

    # The patched PDF should contain all three fake values
    assert str(fake_co2_year) in patched_text, \
        f"PDF with patched SOURCES should contain fake CO2 year {fake_co2_year}"
    assert str(fake_gdp_year) in patched_text, \
        f"PDF with patched SOURCES should contain fake GDP year {fake_gdp_year}"
    assert fake_ren_years in patched_text, \
        f"PDF with patched SOURCES should contain fake renewable years {fake_ren_years}"

    # The original PDF should NOT contain the fake values
    assert str(fake_co2_year) not in original_text, \
        f"Original PDF should not contain fake CO2 year {fake_co2_year}"
    assert str(fake_gdp_year) not in original_text, \
        f"Original PDF should not contain fake GDP year {fake_gdp_year}"
    assert fake_ren_years not in original_text, \
        f"Original PDF should not contain fake renewable years {fake_ren_years}"

    # Both should contain 2022 (HDI year, unchanged in patch)
    assert "2022" in original_text
    assert "2022" in patched_text

    # Both should contain 2024 (original CO2 and GDP year)
    assert "2024" in original_text
    # Patched should NOT contain 2024 (replaced by fake years)
    assert "2024" not in patched_text

    # Both should contain 2011-2021 (original renewable years)
    assert "2011-2021" in original_text
    # Patched should NOT contain 2011-2021 (replaced by fake years)
    assert "2011-2021" not in patched_text


def test_source_register_benchmark_entry_updated():
    """Source register 'benchmark' entry no longer claims no year recorded."""
    from app.ingesters.source_register import SOURCE_REGISTER

    benchmark_entry = SOURCE_REGISTER["benchmark"]
    notes = benchmark_entry.notes.lower()

    # Should NOT claim no year is recorded
    assert "no year" not in notes or "no year recorded" not in notes, \
        "benchmark entry should not claim 'no year recorded'"

    # Should mention the refresh
    assert "refresh" in notes or "world bank" in notes or "undp" in notes, \
        "benchmark entry should mention the refresh or the sources"

    # last_verified_data should now be set (not None)
    assert benchmark_entry.last_verified_data is not None, \
        "benchmark.last_verified_data should be set after refresh"

    # Should mention the years
    lvd = str(benchmark_entry.last_verified_data)
    assert "2024" in lvd or "2022" in lvd, \
        f"benchmark.last_verified_data should mention years, got: {lvd}"


def test_source_register_global_avg_entry_updated():
    """Source register 'global_avg' entry no longer claims no measurement stands behind it."""
    from app.ingesters.source_register import SOURCE_REGISTER

    global_avg_entry = SOURCE_REGISTER["global_avg"]
    notes = global_avg_entry.notes.lower()

    # Should mention World Bank world aggregates
    assert "world bank" in notes or "world aggregate" in notes, \
        "global_avg entry should mention World Bank world aggregates"

    # Should mention UNDP or HDI
    assert "undp" in notes or "hdi" in notes, \
        "global_avg entry should mention UNDP/HDI source"

    # last_verified_data should now be set (not None)
    assert global_avg_entry.last_verified_data is not None, \
        "global_avg.last_verified_data should be set after refresh"

    # Should mention the years
    lvd = str(global_avg_entry.last_verified_data)
    assert "2024" in lvd or "2022" in lvd, \
        f"global_avg.last_verified_data should mention years, got: {lvd}"

    # measurement_kind should now be ADMINISTRATIVE_SNAPSHOT, not STATIC_BASELINE
    # (because three of the values are now from World Bank/UNDP world aggregates)
    from app.ingesters.source_register import ADMINISTRATIVE_SNAPSHOT
    assert global_avg_entry.measurement_kind == ADMINISTRATIVE_SNAPSHOT, \
        f"global_avg.measurement_kind should be ADMINISTRATIVE_SNAPSHOT, got {global_avg_entry.measurement_kind}"


def test_benchmark_values_were_actually_updated():
    """Spot-check that at least one country's values changed from known old values."""
    from app.country_benchmarks import BENCHMARKS
    import json

    # Load the new values JSON to verify changes
    with open('/private/tmp/claude-501/-Users-evgenijpoluhin-sora-earth-ai-platform--claude-worktrees-frosty-archimedes-03665d/de140aed-61d0-4f89-a6b8-8d71b0a13b3b/scratchpad/n16/new_values.json') as f:
        new_values = json.load(f)

    # Check a few countries where values definitely changed
    germany = BENCHMARKS["Germany"]
    assert germany["co2_per_capita"] == 6.9, \
        f"Germany co2_per_capita should be updated to 6.9, got {germany['co2_per_capita']}"
    assert germany["gdp_per_capita"] == 56104, \
        f"Germany gdp_per_capita should be updated to 56104, got {germany['gdp_per_capita']}"
    assert germany["hdi"] == 0.95, \
        f"Germany hdi should be updated to 0.95, got {germany['hdi']}"

    # Verify renewable_share was NOT changed (should still be 46.3 for Germany)
    assert germany["renewable_share"] == 46.3, \
        f"Germany renewable_share should be unchanged at 46.3, got {germany['renewable_share']}"


def test_global_avg_values_updated():
    """GLOBAL_AVG values updated for CO2, GDP, HDI."""
    from app.country_benchmarks import GLOBAL_AVG

    assert GLOBAL_AVG["co2_per_capita"] == 4.7, \
        f"GLOBAL_AVG co2_per_capita should be 4.7, got {GLOBAL_AVG['co2_per_capita']}"
    assert GLOBAL_AVG["gdp_per_capita"] == 13717, \
        f"GLOBAL_AVG gdp_per_capita should be 13717, got {GLOBAL_AVG['gdp_per_capita']}"
    assert GLOBAL_AVG["hdi"] == 0.739, \
        f"GLOBAL_AVG hdi should be 0.739, got {GLOBAL_AVG['hdi']}"

    # renewable_share should be unchanged
    assert GLOBAL_AVG["renewable_share"] == 28.3, \
        f"GLOBAL_AVG renewable_share should be unchanged at 28.3, got {GLOBAL_AVG['renewable_share']}"
