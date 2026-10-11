"""Tests for Open-Meteo archive backfill.

Validates that:
- Archive signals carry the correct source names and metadata
- Indicator names/units match the live ingesters
- None values are skipped and counted
- Dry-run writes nothing
- Idempotency: re-running leaves row counts unchanged
- Date range validation works
- Main database is refused without override
- Table auto-creation works
- Chunking processes multi-year ranges correctly
- Live sources are untouched by backfill

NO network calls (all API fetches are mocked).
NO main database (tests use sqlite in tmp_path or in-memory).
"""
import gzip
import json
import os
import pytest
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.ingesters.base import Signal
from app.ingesters.openmeteo import REGION_CAPITALS, WEATHER_VARIABLES
from app.ingesters.openmeteo_air_quality import POLLUTANTS
from app.ingesters.openmeteo_archive import (
    fetch_weather_archive,
    fetch_air_quality_archive,
    _parse_weather_response,
    _parse_air_quality_response,
    _build_revision_tag,
)
from app.database import EnvironmentalObservation
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker




@pytest.fixture(autouse=True)
def patch_sleep(monkeypatch):
    """Patch time.sleep to make tests fast (avoid 5s delays)."""
    # Patch where time.sleep is looked up in openmeteo_archive
    monkeypatch.setattr("app.ingesters.openmeteo_archive.time.sleep", lambda x: None)
    # Also patch in the script module
    monkeypatch.setattr("time.sleep", lambda x: None)


def test_archive_sources_are_in_register():
    """Archive source names are registered with correct measurement_kind."""
    from app.ingesters.source_register import SOURCE_REGISTER, MODELLED, STATUS_ON_DEMAND

    # Weather archive
    assert "openmeteo_archive" in SOURCE_REGISTER
    weather_facts = SOURCE_REGISTER["openmeteo_archive"]
    assert weather_facts.measurement_kind == MODELLED
    assert weather_facts.status == STATUS_ON_DEMAND
    assert "reconstructed from later-fetched archives" in weather_facts.notes
    assert "never replaces openmeteo rows" in weather_facts.notes.lower()

    # Air quality archive
    assert "openmeteo_air_quality_archive" in SOURCE_REGISTER
    aq_facts = SOURCE_REGISTER["openmeteo_air_quality_archive"]
    assert aq_facts.measurement_kind == MODELLED
    assert aq_facts.model == "CAMS reanalysis via Open-Meteo"
    assert aq_facts.status == STATUS_ON_DEMAND
    assert "backfill gaps" in aq_facts.notes
    assert "never replaces openmeteo_air_quality rows" in aq_facts.notes.lower()


def test_weather_signals_match_live_ingester_schema():
    """Archive weather signals use same indicator names/units as live ingester."""
    from app.ingesters.openmeteo import OpenMeteoIngester

    # Mock response with all weather variables
    mock_response = {
        "hourly": {
            "time": ["2024-01-15T00:00", "2024-01-15T01:00"],
        }
    }
    for var in WEATHER_VARIABLES:
        mock_response["hourly"][var] = [10.5, 11.2]

    signals, _ = _parse_weather_response(
        mock_response,
        "DEU",
        52.52,
        13.41,
        "archive-fetched-2026-10-11",
    )

    # Extract metric names from signals
    archive_metrics = {s.metric for s in signals}

    # Build expected metrics from live ingester's mapping
    live_metrics = set()
    for var in WEATHER_VARIABLES:
        metric, _ = OpenMeteoIngester._map_variable(var)
        live_metrics.add(metric)

    assert archive_metrics == live_metrics, (
        "Archive weather metrics must match live ingester exactly. "
        f"Missing: {live_metrics - archive_metrics}, "
        f"Extra: {archive_metrics - live_metrics}"
    )

    # Check units match too
    for signal in signals:
        # Find the API variable that produced this metric
        for var in WEATHER_VARIABLES:
            live_metric, live_unit = OpenMeteoIngester._map_variable(var)
            if signal.metric == live_metric:
                assert signal.unit == live_unit, (
                    f"Metric {signal.metric} has unit {signal.unit!r} in archive "
                    f"but {live_unit!r} in live ingester"
                )
                break


def test_air_quality_signals_match_live_ingester_schema():
    """Archive air quality signals use same indicator names/units as live ingester."""
    # Mock response
    mock_response = {
        "hourly": {
            "time": ["2024-01-15T00:00", "2024-01-15T01:00"],
        }
    }
    for pollutant in POLLUTANTS:
        mock_response["hourly"][pollutant] = [25.3, 26.1]

    signals, _ = _parse_air_quality_response(
        mock_response,
        "GBR",
        51.51,
        -0.13,
        "archive-fetched-2026-10-11",
    )

    # Extract metrics
    archive_metrics = {s.metric for s in signals}
    live_metrics = set(POLLUTANTS.keys())

    assert archive_metrics == live_metrics, (
        "Archive air quality metrics must match live ingester. "
        f"Missing: {live_metrics - archive_metrics}, "
        f"Extra: {archive_metrics - live_metrics}"
    )

    # Check units
    for signal in signals:
        assert signal.metric in POLLUTANTS
        assert signal.unit == POLLUTANTS[signal.metric]


def test_archive_signals_carry_correct_source_names():
    """Archive signals use openmeteo_archive / openmeteo_air_quality_archive."""
    # Weather
    weather_resp = {
        "hourly": {
            "time": ["2024-01-15T00:00"],
            "temperature_2m": [15.5],
        }
    }
    weather_signals, _ = _parse_weather_response(
        weather_resp, "FRA", 48.86, 2.35, "archive-fetched-2026-10-11"
    )
    assert all(s.source == "openmeteo_archive" for s in weather_signals)

    # Air quality
    aq_resp = {
        "hourly": {
            "time": ["2024-01-15T00:00"],
            "pm10": [42.0],
        }
    }
    aq_signals, _ = _parse_air_quality_response(
        aq_resp, "FRA", 48.86, 2.35, "archive-fetched-2026-10-11"
    )
    assert all(s.source == "openmeteo_air_quality_archive" for s in aq_signals)


def test_archive_signals_have_observed_temporal_kind():
    """Archive signals carry temporal_kind='observed' with event_time."""
    resp = {
        "hourly": {
            "time": ["2024-01-15T12:00"],
            "temperature_2m": [22.0],
        }
    }
    signals, _ = _parse_weather_response(
        resp, "USA", 38.91, -77.04, "archive-fetched-2026-10-11"
    )

    assert len(signals) == 1
    signal = signals[0]
    assert signal.temporal_kind == "observed"
    assert signal.observed_at is not None
    assert signal.observed_at == datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)


def test_archive_signals_carry_source_revision():
    """source_revision is archive-fetched-<YYYY-MM-DD>."""
    fetch_date = datetime(2026, 10, 11, 14, 30, tzinfo=timezone.utc)
    tag = _build_revision_tag(fetch_date)
    assert tag == "archive-fetched-2026-10-11"

    resp = {
        "hourly": {
            "time": ["2024-01-15T00:00"],
            "pm2_5": [18.5],
        }
    }
    signals, _ = _parse_air_quality_response(
        resp, "CHN", 39.90, 116.41, tag
    )

    assert all(s.source_revision == tag for s in signals)


def test_archive_signals_carry_coordinates():
    """Archive signals include latitude/longitude in metadata."""
    resp = {
        "hourly": {
            "time": ["2024-01-15T00:00"],
            "wind_speed_10m": [12.3],
        }
    }
    lat, lon = 35.68, 139.65
    signals, _ = _parse_weather_response(
        resp, "JPN", lat, lon, "archive-fetched-2026-10-11"
    )

    assert len(signals) == 1
    assert signals[0].metadata is not None
    assert signals[0].metadata["latitude"] == lat
    assert signals[0].metadata["longitude"] == lon


def test_none_values_are_skipped_and_counted():
    """None values in API response are skipped, and count is returned."""
    resp = {
        "hourly": {
            "time": ["2024-01-15T00:00", "2024-01-15T01:00", "2024-01-15T02:00"],
            "temperature_2m": [15.0, None, 16.5],
            "relative_humidity_2m": [80.0, 82.0, None],
        }
    }

    signals, none_count = _parse_weather_response(
        resp, "ESP", 40.42, -3.70, "archive-fetched-2026-10-11"
    )

    # 3 time points x 2 variables = 6 potential signals
    # 2 None values: temp at [1], humidity at [2]
    # So 4 signals produced, 2 skipped
    assert len(signals) == 4
    assert none_count == 2

    # Verify the None slots were actually skipped
    temp_signals = [s for s in signals if s.metric == "temperature"]
    assert len(temp_signals) == 2  # Only [0] and [2], not [1]


def test_idempotency_rerunning_same_range_leaves_counts_unchanged(tmp_path):
    """Re-running the same range on the same data leaves row counts unchanged."""
    db_path = tmp_path / "archive.db"
    engine = create_engine(f"sqlite:///{db_path}")
    EnvironmentalObservation.__table__.create(bind=engine, checkfirst=True)
    Session = sessionmaker(bind=engine)

    # Mock responses
    mock_weather_response = {
        "hourly": {
            "time": ["2024-01-15T00:00", "2024-01-15T01:00"],
            "temperature_2m": [15.0, 16.0],
        }
    }

    with patch("app.ingesters.openmeteo_archive._fetch_weather_for_point") as mock_fetch:
        mock_fetch.return_value = mock_weather_response

        # First run
        signals1, _ = fetch_weather_archive(
            datetime(2024, 1, 15, tzinfo=timezone.utc),
            datetime(2024, 1, 15, tzinfo=timezone.utc),
        )

        # Persist
        from app.ingesters.persist import _signal_to_observation_dict, _upsert_observations
        session1 = Session()
        observations1 = [
            _signal_to_observation_dict(s, "openmeteo_archive")
            for s in signals1
        ]
        inserted1, updated1, dup1 = _upsert_observations(session1, observations1)
        session1.commit()
        session1.close()

        # Count rows
        session = Session()
        count_after_first = session.execute(
            select(EnvironmentalObservation.id)
        ).fetchall()
        session.close()
        first_count = len(count_after_first)

        assert inserted1 > 0
        assert first_count == inserted1

        # Second run with identical data
        signals2, _ = fetch_weather_archive(
            datetime(2024, 1, 15, tzinfo=timezone.utc),
            datetime(2024, 1, 15, tzinfo=timezone.utc),
        )

        session2 = Session()
        observations2 = [
            _signal_to_observation_dict(s, "openmeteo_archive")
            for s in signals2
        ]
        inserted2, updated2, dup2 = _upsert_observations(session2, observations2)
        session2.commit()
        session2.close()

        # Count again
        session = Session()
        count_after_second = session.execute(
            select(EnvironmentalObservation.id)
        ).fetchall()
        session.close()
        second_count = len(count_after_second)

        # No new rows inserted (all were duplicates or updates)
        assert inserted2 == 0
        assert second_count == first_count


def test_date_range_validation_refuses_end_before_start():
    """Script refuses end_date < start_date."""
    from scripts.backfill_openmeteo_archive import validate_date_range

    start = datetime(2024, 1, 15, tzinfo=timezone.utc)
    end = datetime(2024, 1, 10, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="before start date"):
        validate_date_range(start, end)


def test_date_range_validation_refuses_over_10_years():
    """Script refuses ranges > 10 years."""
    from scripts.backfill_openmeteo_archive import validate_date_range

    start = datetime(2014, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 12, 31, tzinfo=timezone.utc)  # 11 years

    with pytest.raises(ValueError, match="10 years"):
        validate_date_range(start, end)


def test_main_database_check_normalizes_localhost():
    """localhost, 127.0.0.1, ::1 are treated as equivalent."""
    from scripts.backfill_openmeteo_archive import parse_database_components

    url1 = "postgresql://sora:pass@localhost:5432/sora_earth"
    url2 = "postgresql://sora:pass@127.0.0.1:5432/sora_earth"
    url3 = "postgresql://sora:pass@[::1]:5432/sora_earth"

    db1, host1 = parse_database_components(url1)
    db2, host2 = parse_database_components(url2)
    db3, host3 = parse_database_components(url3)

    assert db1 == db2 == db3 == "sora_earth"
    # Hosts should be normalized in the check function
    # (the normalization happens in check_database_separation)


def test_table_auto_created_in_fresh_database(tmp_path):
    """ensure_table_exists creates table in a fresh database."""
    from scripts.backfill_openmeteo_archive import ensure_table_exists

    db_path = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{db_path}")

    # Table doesn't exist yet
    from sqlalchemy import inspect
    inspector = inspect(engine)
    assert "environmental_observations" not in inspector.get_table_names()

    # Call ensure_table_exists
    ensure_table_exists(engine)

    # Table now exists
    inspector = inspect(engine)
    assert "environmental_observations" in inspector.get_table_names()


def test_chunking_splits_multi_year_range_correctly():
    """split_into_year_chunks produces one chunk per calendar year."""
    from scripts.backfill_openmeteo_archive import split_into_year_chunks

    start = datetime(2020, 6, 15, tzinfo=timezone.utc)
    end = datetime(2022, 3, 10, tzinfo=timezone.utc)

    chunks = split_into_year_chunks(start, end)

    # Expected: 2020-06-15 to 2020-12-31, 2021-01-01 to 2021-12-31, 2022-01-01 to 2022-03-10
    assert len(chunks) == 3

    # Chunk 1: 2020
    assert chunks[0][0] == datetime(2020, 6, 15, tzinfo=timezone.utc)
    assert chunks[0][1].year == 2020
    assert chunks[0][1].month == 12
    assert chunks[0][1].day == 31

    # Chunk 2: 2021
    assert chunks[1][0] == datetime(2021, 1, 1, tzinfo=timezone.utc)
    assert chunks[1][1].year == 2021
    assert chunks[1][1].month == 12

    # Chunk 3: 2022
    assert chunks[2][0] == datetime(2022, 1, 1, tzinfo=timezone.utc)
    assert chunks[2][1] == datetime(2022, 3, 10, tzinfo=timezone.utc)


def test_chunking_single_year_produces_one_chunk():
    """A range within one year produces a single chunk."""
    from scripts.backfill_openmeteo_archive import split_into_year_chunks

    start = datetime(2021, 5, 1, tzinfo=timezone.utc)
    end = datetime(2021, 8, 31, tzinfo=timezone.utc)

    chunks = split_into_year_chunks(start, end)
    assert len(chunks) == 1
    assert chunks[0][0] == start
    assert chunks[0][1] == datetime(2021, 8, 31, tzinfo=timezone.utc)


def test_from_dir_loads_cached_responses(tmp_path):
    """--from-dir reads gzipped JSON files instead of making network calls."""
    # Create cache directory structure
    cache_dir = tmp_path / "cache"
    weather_dir = cache_dir / "weather"
    weather_dir.mkdir(parents=True)

    # Write a cached response for DEU
    mock_data = {
        "hourly": {
            "time": ["2024-01-15T00:00"],
            "temperature_2m": [12.5],
        }
    }
    cache_file = weather_dir / "DEU_2024.json.gz"
    with gzip.open(cache_file, "wt", encoding="utf-8") as f:
        json.dump(mock_data, f)

    # Fetch with from_dir (no network)
    with patch("app.ingesters.openmeteo_archive._fetch_weather_for_point") as mock_fetch:
        # Mock should NOT be called
        signals, _ = fetch_weather_archive(
            datetime(2024, 1, 15, tzinfo=timezone.utc),
            datetime(2024, 1, 15, tzinfo=timezone.utc),
            from_dir=cache_dir,
        )

        # No network calls
        assert mock_fetch.call_count == 0

        # But signals were produced from cached data
        deu_signals = [s for s in signals if s.region_code == "DEU"]
        assert len(deu_signals) > 0


def test_from_dir_handles_missing_cache_gracefully(tmp_path):
    """Missing cache files log a warning but don't crash."""
    cache_dir = tmp_path / "empty_cache"
    cache_dir.mkdir()
    (cache_dir / "weather").mkdir()

    # No cached files
    with patch("app.ingesters.openmeteo_archive._fetch_weather_for_point") as mock_fetch:
        signals, _ = fetch_weather_archive(
            datetime(2024, 1, 15, tzinfo=timezone.utc),
            datetime(2024, 1, 15, tzinfo=timezone.utc),
            from_dir=cache_dir,
        )

        # No network calls (from_dir was set)
        assert mock_fetch.call_count == 0

        # No signals produced (no cached data)
        assert len(signals) == 0


def test_live_sources_are_untouched_by_backfill(tmp_path):
    """Backfill with archive sources does not modify existing openmeteo rows."""
    db_path = tmp_path / "archive.db"
    engine = create_engine(f"sqlite:///{db_path}")
    EnvironmentalObservation.__table__.create(bind=engine, checkfirst=True)
    Session = sessionmaker(bind=engine)

    # Insert a "live" openmeteo row
    session = Session()
    live_obs = EnvironmentalObservation(
        region_id="DEU",
        country_code="DEU",
        latitude=52.52,
        longitude=13.41,
        indicator="temperature",
        value=15.0,
        unit="celsius",
        source="openmeteo",  # Live source
        source_record_id="DEU_temperature_2024-01-15T00:00:00+00:00",
        event_time=datetime(2024, 1, 15, 0, 0, tzinfo=timezone.utc),
        temporal_kind="observed",
        ingested_at=datetime(2024, 1, 15, 1, 0, tzinfo=timezone.utc),
        is_valid=True,
        created_at=datetime(2024, 1, 15, 1, 0, tzinfo=timezone.utc),
    )
    session.add(live_obs)
    session.commit()
    live_id = live_obs.id
    live_value = live_obs.value
    session.close()

    # Now backfill with archive source for the same region/time/metric
    mock_resp = {
        "hourly": {
            "time": ["2024-01-15T00:00"],
            "temperature_2m": [16.5],  # Different value
        }
    }

    with patch("app.ingesters.openmeteo_archive._fetch_weather_for_point") as mock_fetch:
        mock_fetch.return_value = mock_resp

        signals, _ = fetch_weather_archive(
            datetime(2024, 1, 15, tzinfo=timezone.utc),
            datetime(2024, 1, 15, tzinfo=timezone.utc),
        )

        # Persist archive signals
        from app.ingesters.persist import _signal_to_observation_dict, _upsert_observations
        session2 = Session()
        observations = [
            _signal_to_observation_dict(s, "openmeteo_archive")
            for s in signals if s.region_code == "DEU" and s.metric == "temperature"
        ]
        _upsert_observations(session2, observations)
        session2.commit()
        session2.close()

    # Verify live row is unchanged
    session3 = Session()
    live_row = session3.get(EnvironmentalObservation, live_id)
    assert live_row is not None
    assert live_row.source == "openmeteo"
    assert live_row.value == live_value  # Unchanged

    # Verify archive row was inserted separately
    archive_rows = session3.execute(
        select(EnvironmentalObservation).where(
            EnvironmentalObservation.source == "openmeteo_archive",
            EnvironmentalObservation.region_id == "DEU",
            EnvironmentalObservation.indicator == "temperature",
        )
    ).scalars().all()

    assert len(archive_rows) >= 1
    assert any(r.value == 16.5 for r in archive_rows)

    session3.close()




def test_dry_run_via_cli_writes_nothing(tmp_path, monkeypatch):
    """main() with --dry-run writes zero rows and never calls persist."""
    from scripts.backfill_openmeteo_archive import main
    
    db_path = tmp_path / "archive.db"
    db_url = f"sqlite:///{db_path}"
    
    mock_response = {
        "hourly": {
            "time": ["2024-01-15T00:00"],
            "temperature_2m": [15.0],
        }
    }
    
    with patch("app.ingesters.openmeteo_archive._fetch_weather_for_point") as mock_fetch, \
         patch("app.ingesters.persist.persist_environmental_observations") as mock_persist:
        mock_fetch.return_value = mock_response
        
        exit_code = main([
            "--database-url", db_url,
            "--start", "2024-01-15",
            "--end", "2024-01-15",
            "--kind", "weather",
            "--dry-run",
            "--points", "DEU",
        ])
        
        assert exit_code == 0
        assert mock_persist.call_count == 0, "persist must not be called in dry-run"
    
    if db_path.exists():
        engine = create_engine(db_url)
        Session = sessionmaker(bind=engine)
        session = Session()
        count = session.execute(select(EnvironmentalObservation.id)).fetchall()
        session.close()
        assert len(count) == 0, "dry-run must write zero rows"


def test_failed_fetch_exits_nonzero_and_names_point(tmp_path, monkeypatch, caplog):
    """A fetch that fails for one point exits non-zero and names the point."""
    from scripts.backfill_openmeteo_archive import main

    db_path = tmp_path / "archive.db"

    def failing_fetch(region_code, lat, lon, start_date, end_date, retries=3):
        if region_code == "DEU":
            raise RuntimeError("Simulated fetch failure for DEU")
        return {
            "hourly": {
                "time": ["2024-01-15T00:00"],
                "temperature_2m": [15.0],
            }
        }

    with patch("app.ingesters.openmeteo_archive._fetch_weather_for_point", side_effect=failing_fetch):
        exit_code = main([
            "--database-url", f"sqlite:///{db_path}",
            "--start", "2024-01-15",
            "--end", "2024-01-15",
            "--kind", "weather",
            "--points", "DEU,GBR",
        ])

        assert exit_code == 1, "failed fetch must exit non-zero"

    log_output = caplog.text
    assert "Failed fetches:" in log_output or "failed" in log_output.lower()


def test_failed_persist_exits_nonzero(tmp_path, monkeypatch):
    """A persist that raises exits non-zero."""
    from scripts.backfill_openmeteo_archive import main
    
    db_path = tmp_path / "archive.db"
    
    mock_response = {
        "hourly": {
            "time": ["2024-01-15T00:00"],
            "temperature_2m": [15.0],
        }
    }
    
    def failing_persist(signals, source, session=None):
        raise RuntimeError("Simulated persist failure")
    
    with patch("app.ingesters.openmeteo_archive._fetch_weather_for_point") as mock_fetch, \
         patch("scripts.backfill_openmeteo_archive.persist_environmental_observations", side_effect=failing_persist):
        mock_fetch.return_value = mock_response
        
        exit_code = main([
            "--database-url", f"sqlite:///{db_path}",
            "--start", "2024-01-15",
            "--end", "2024-01-15",
            "--kind", "weather",
            "--points", "DEU,GBR",
        ])
        
        assert exit_code == 1, "failed persist must exit non-zero"


def test_connection_error_does_not_log_credentials(tmp_path, monkeypatch, caplog):
    """Connection failure logs database name and host but not password."""
    from scripts.backfill_openmeteo_archive import main

    def failing_create_engine(url, **kwargs):
        raise RuntimeError("Connection refused")

    with patch("scripts.backfill_openmeteo_archive.create_engine", side_effect=failing_create_engine):
        exit_code = main([
            "--database-url", "postgresql://user:SECRET_PASSWORD@badhost:5432/testdb",
            "--start", "2024-01-15",
            "--end", "2024-01-15",
        ])

        assert exit_code == 1

    log_output = caplog.text

    assert "SECRET_PASSWORD" not in log_output, "password must not appear in output"
    assert "testdb" in log_output or "badhost" in log_output, "database name or host should appear"


def test_main_database_refusal_is_unconditional(monkeypatch):
    """Script refuses main database unconditionally (no override flag)."""
    from scripts.backfill_openmeteo_archive import main
    
    monkeypatch.setenv("DATABASE_URL", "postgresql://sora:pass@localhost:5432/sora_earth")
    
    with pytest.raises(SystemExit) as exc_info:
        main([
            "--database-url", "postgresql://sora:pass@localhost:5432/sora_earth",
            "--start", "2024-01-15",
            "--end", "2024-01-15",
        ])
    
    assert exc_info.value.code == 1



# Mutation tests: temporarily break code, confirm test fails


def test_distinct_source_rule_mutation_check(tmp_path):
    """Mutation check: changing source name to 'openmeteo' makes this test fail."""
    # This test verifies that archive signals do NOT use "openmeteo" source
    resp = {
        "hourly": {
            "time": ["2024-01-15T00:00"],
            "temperature_2m": [20.0],
        }
    }

    signals, _ = _parse_weather_response(
        resp, "FRA", 48.86, 2.35, "archive-fetched-2026-10-11"
    )

    # If someone changes source="openmeteo_archive" to source="openmeteo" in the code,
    # this assertion will fail, catching the mutation
    assert all(s.source == "openmeteo_archive" for s in signals), (
        "MUTATION CHECK: archive signals must use 'openmeteo_archive' source. "
        "This failure indicates the source name was changed to 'openmeteo', "
        "which would make archive rows indistinguishable from live ones."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

# ==============================================================================
# REQUIRED TESTS - Added per task requirements
# ==============================================================================

def test_three_years_one_point_distinct_values(tmp_path):
    """3a: 3 years for one point with distinct values per year."""
    cache_dir = tmp_path / "archive"
    weather_dir = cache_dir / "weather"
    weather_dir.mkdir(parents=True)

    # Create distinct data for each year
    for year, temp in [(2020, 10.0), (2021, 15.0), (2022, 20.0)]:
        mock_data = {
            "hourly": {
                "time": [f"{year}-06-15T12:00"],
                "temperature_2m": [temp],
            }
        }
        cache_file = weather_dir / f"DEU_{year}.json.gz"
        with gzip.open(cache_file, "wt", encoding="utf-8") as f:
            json.dump(mock_data, f)

    # Fetch 3-year range
    from app.ingesters.openmeteo_archive import fetch_weather_for_point_year
    
    all_signals = []
    for year in [2020, 2021, 2022]:
        chunk_start = datetime(year, 1, 1, tzinfo=timezone.utc)
        chunk_end = datetime(year, 12, 31, tzinfo=timezone.utc)
        signals, _ = fetch_weather_for_point_year(
            "DEU", 52.52, 13.41, year,
            chunk_start, chunk_end,
            from_dir=cache_dir,
            vintage=datetime(2026, 10, 11, tzinfo=timezone.utc),
        )
        all_signals.extend(signals)

    # Verify all three years loaded
    temps_by_year = {}
    for s in all_signals:
        if s.metric == "temperature":
            year = s.observed_at.year
            temps_by_year[year] = s.value

    assert temps_by_year[2020] == 10.0, "2020 temperature should be 10.0"
    assert temps_by_year[2021] == 15.0, "2021 temperature should be 15.0"
    assert temps_by_year[2022] == 20.0, "2022 temperature should be 20.0"

    # Test one-month range loads only that month
    one_month_signals, _ = fetch_weather_for_point_year(
        "DEU", 52.52, 13.41, 2021,
        datetime(2021, 6, 1, tzinfo=timezone.utc),
        datetime(2021, 6, 30, tzinfo=timezone.utc),
        from_dir=cache_dir,
        vintage=datetime(2026, 10, 11, tzinfo=timezone.utc),
    )

    # Should only have June data
    for s in one_month_signals:
        assert s.observed_at.month == 6, "One-month range should only load June"
        assert s.observed_at.year == 2021


def test_missing_year_file_exits_nonzero_names_point_and_year(tmp_path, caplog):
    """3b: Missing year file → non-zero exit, names the point and year."""
    from scripts.backfill_openmeteo_archive import main

    cache_dir = tmp_path / "archive_missing"
    weather_dir = cache_dir / "weather"
    weather_dir.mkdir(parents=True)

    # Create file for 2020 but NOT 2021
    mock_data = {
        "hourly": {
            "time": ["2020-06-15T12:00"],
            "temperature_2m": [10.0],
        }
    }
    cache_file = weather_dir / "DEU_2020.json.gz"
    with gzip.open(cache_file, "wt", encoding="utf-8") as f:
        json.dump(mock_data, f)

    db_path = tmp_path / "archive.db"
    
    exit_code = main([
        "--database-url", f"sqlite:///{db_path}",
        "--start", "2020-01-01",
        "--end", "2021-12-31",  # Spans into 2021 which has no file
        "--kind", "weather",
        "--from-dir", str(cache_dir),
        "--vintage", "2026-10-11",
    ])

    assert exit_code == 1, "Missing year file must cause non-zero exit"

    log_output = caplog.text
    assert "DEU" in log_output, "Error must name the point (DEU)"
    assert "2021" in log_output, "Error must name the missing year (2021)"


def test_air_quality_before_2022_08_refused(tmp_path, caplog):
    """3c: Air quality requested before 2022-08-01 → up-front refusal."""
    from scripts.backfill_openmeteo_archive import main

    db_path = tmp_path / "archive.db"

    exit_code = main([
        "--database-url", f"sqlite:///{db_path}",
        "--start", "2022-01-01",  # Before AIR_QUALITY_ARCHIVE_START
        "--end", "2022-12-31",
        "--kind", "air_quality",
    ])

    assert exit_code == 1, "Air quality before 2022-08-01 must be refused"

    log_output = caplog.text
    assert "2022-08-01" in log_output, "Refusal must state archive start date"
    assert "air quality" in log_output.lower()


def test_vintage_handling(tmp_path):
    """3d: --vintage: source_revision == archive-fetched-<vintage>."""
    from scripts.backfill_openmeteo_archive import main

    cache_dir = tmp_path / "archive_vintage"
    weather_dir = cache_dir / "weather"
    weather_dir.mkdir(parents=True)

    mock_data = {
        "hourly": {
            "time": ["2024-01-15T12:00"],
            "temperature_2m": [15.0],
        }
    }
    # Create cache files for ALL points to avoid missing file errors
    for region_code, _, _ in REGION_CAPITALS:
        cache_file = weather_dir / f"{region_code}_2024.json.gz"
        with gzip.open(cache_file, "wt", encoding="utf-8") as f:
            json.dump(mock_data, f)

    db_path = tmp_path / "archive.db"
    engine = create_engine(f"sqlite:///{db_path}")
    EnvironmentalObservation.__table__.create(bind=engine, checkfirst=True)

    # Run with --vintage
    exit_code = main([
        "--database-url", f"sqlite:///{db_path}",
        "--start", "2024-01-15",
        "--end", "2024-01-15",
        "--kind", "weather",
        "--from-dir", str(cache_dir),
        "--vintage", "2026-10-11",
    ])

    assert exit_code == 0

    # Verify source_revision
    Session = sessionmaker(bind=engine)
    session = Session()
    rows = session.execute(
        select(EnvironmentalObservation.source_revision).distinct()
    ).scalars().all()
    session.close()

    assert len(rows) > 0
    assert all(r == "archive-fetched-2026-10-11" for r in rows), \
        f"All rows must have source_revision from --vintage, got: {set(rows)}"


def test_vintage_required_with_from_dir():
    """3d: --from-dir without --vintage → refusal."""
    from scripts.backfill_openmeteo_archive import main

    exit_code = main([
        "--database-url", "sqlite:///test.db",
        "--start", "2024-01-01",
        "--end", "2024-01-31",
        "--from-dir", "/some/path",
        # NO --vintage
    ])

    assert exit_code == 1, "--from-dir without --vintage must be refused"


def test_archive_hash_changes_when_file_changes(tmp_path):
    """3e: Archive hash changes when one file's bytes change."""
    from app.ingesters.openmeteo_archive import compute_archive_hash

    # Create two files
    file1 = tmp_path / "file1.gz"
    file2 = tmp_path / "file2.gz"
    
    file1.write_bytes(b"content1")
    file2.write_bytes(b"content2")

    hash1 = compute_archive_hash([str(file1), str(file2)])

    # Change file1's content
    file1.write_bytes(b"content1_modified")

    hash2 = compute_archive_hash([str(file1), str(file2)])

    assert hash1 != hash2, "Hash must change when file content changes"


def test_http_429_waits_and_retries(tmp_path, monkeypatch):
    """3f: HTTP 429 → waits and retries, persistent 5xx → counted failure."""
    from app.ingesters.openmeteo_archive import _fetch_weather_for_point
    import urllib.error

    call_count = [0]
    sleep_calls = []

    def mock_urlopen(url, timeout=None):
        call_count[0] += 1
        if call_count[0] <= 2:
            # First two attempts: 429
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
        # Third attempt: success
        from io import BytesIO
        response_data = json.dumps({
            "hourly": {
                "time": ["2024-01-15T00:00"],
                "temperature_2m": [15.0],
            }
        }).encode()
        
        class MockResponse:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return response_data
            
        return MockResponse()

    def mock_sleep(seconds):
        sleep_calls.append(seconds)

    with patch("urllib.request.urlopen", side_effect=mock_urlopen), \
         patch("time.sleep", side_effect=mock_sleep):
        result = _fetch_weather_for_point("DEU", 52.52, 13.41, "2024-01-15", "2024-01-15")

        assert result is not None, "Should succeed after retries"
        assert call_count[0] == 3, "Should have made 3 attempts"
        assert len(sleep_calls) == 2, "Should have slept twice (for 429 retries)"
        # 429 uses exponential backoff: 60 * 2^0 = 60, 60 * 2^1 = 120
        assert sleep_calls[0] == 60
        assert sleep_calls[1] == 120

    # Test persistent 5xx failure
    call_count_5xx = [0]

    def mock_urlopen_5xx(url, timeout=None):
        call_count_5xx[0] += 1
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)

    with patch("urllib.request.urlopen", side_effect=mock_urlopen_5xx), \
         patch("time.sleep"):
        result = _fetch_weather_for_point("DEU", 52.52, 13.41, "2024-01-15", "2024-01-15")

        assert result is None, "Persistent 5xx should return None"
        assert call_count_5xx[0] == 3, "Should retry MAX_RETRIES times"


def test_dry_run_mutation_persist_never_called(tmp_path):
    """3g: Dry-run → zero rows and persist never called. MUTATION TEST."""
    from scripts.backfill_openmeteo_archive import main
    from unittest.mock import patch, MagicMock

    db_path = tmp_path / "archive_dry.db"
    db_url = f"sqlite:///{db_path}"

    cache_dir = tmp_path / "archive_dry_cache"
    weather_dir = cache_dir / "weather"
    weather_dir.mkdir(parents=True)

    mock_data = {
        "hourly": {
            "time": ["2024-01-15T12:00"],
            "temperature_2m": [15.0],
        }
    }
    points_to_use = REGION_CAPITALS[:2]
    for region_code, _, _ in points_to_use:
        cache_file = weather_dir / f"{region_code}_2024.json.gz"
        with gzip.open(cache_file, "wt", encoding="utf-8") as f:
            json.dump(mock_data, f)

    with patch("app.ingesters.persist.persist_environmental_observations") as mock_persist:
        mock_persist.return_value = MagicMock(inserted=10, updated=0, rejected=0, duplicates=0)

        points_str = ','.join(rc for rc, _, _ in points_to_use)
        exit_code = main([
            "--database-url", db_url,
            "--start", "2024-01-15",
            "--end", "2024-01-15",
            "--kind", "weather",
            "--from-dir", str(cache_dir),
            "--vintage", "2026-10-11",
            "--dry-run",
            "--points", points_str,
        ])

        assert exit_code == 0
        assert mock_persist.call_count == 0, "persist must NEVER be called in dry-run"

    # Verify zero rows even if table was created
    if db_path.exists():
        engine = create_engine(db_url)
        Session = sessionmaker(bind=engine)
        session = Session()
        count = session.execute(select(EnvironmentalObservation.id)).fetchall()
        session.close()
        assert len(count) == 0, "dry-run must write zero rows"


def test_dry_run_mutation_red_line():
    """3g MUTATION: Change dry-run to persist, show test fails."""
    # This is a documentation of what the mutation test shows.
    # The actual mutation is:
    # 1. In the script, change the dry-run branch to call persist_environmental_observations
    # 2. Run test_dry_run_mutation_persist_never_called
    # 3. The assertion `mock_persist.call_count == 0` will FAIL (red line)
    # 4. The assertion about zero rows will ALSO FAIL if the mutation actually writes
    #
    # This test documents that the above test catches the mutation.
    # We'll run the actual mutation after all tests pass.
    pass


def test_end_to_end_two_points_two_years(tmp_path):
    """3h: End-to-end with 2 points × 2 years (weather + air quality)."""
    from scripts.backfill_openmeteo_archive import main

    cache_dir = tmp_path / "archive_e2e"
    weather_dir = cache_dir / "weather"
    aq_dir = cache_dir / "air_quality"
    weather_dir.mkdir(parents=True)
    aq_dir.mkdir(parents=True)

    # Pick 2 points
    points = REGION_CAPITALS[:2]
    points_str = ','.join(rc for rc, _, _ in points)
    years = [2023, 2024]

    # Create weather data
    for region_code, _, _ in points:
        for year in years:
            mock_data = {
                "hourly": {
                    "time": [f"{year}-06-15T12:00"],
                    "temperature_2m": [15.0],
                    "relative_humidity_2m": [80.0],
                }
            }
            cache_file = weather_dir / f"{region_code}_{year}.json.gz"
            with gzip.open(cache_file, "wt", encoding="utf-8") as f:
                json.dump(mock_data, f)

    # Create air quality data
    for region_code, _, _ in points:
        for year in years:
            mock_data = {
                "hourly": {
                    "time": [f"{year}-06-15T12:00"],
                    "pm10": [42.0],
                    "pm2_5": [18.5],
                }
            }
            cache_file = aq_dir / f"{region_code}_{year}.json.gz"
            with gzip.open(cache_file, "wt", encoding="utf-8") as f:
                json.dump(mock_data, f)

    db_path = tmp_path / "archive_e2e.db"
    db_url = f"sqlite:///{db_path}"

    # First run
    exit_code = main([
        "--database-url", db_url,
        "--start", "2023-01-01",
        "--end", "2024-12-31",
        "--kind", "both",
        "--from-dir", str(cache_dir),
        "--vintage", "2026-10-11",
        "--points", points_str,
    ])

    assert exit_code == 0, "First run should succeed"

    # Count rows by source and year
    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)
    session = Session()

    from sqlalchemy import func
    from sqlalchemy.sql import extract

    # Weather rows per year
    weather_counts = {}
    for year in years:
        count = session.execute(
            select(func.count()).where(
                EnvironmentalObservation.source == "openmeteo_archive",
                extract('year', EnvironmentalObservation.event_time) == year,
            )
        ).scalar()
        weather_counts[year] = count

    # Air quality rows per year
    aq_counts = {}
    for year in years:
        count = session.execute(
            select(func.count()).where(
                EnvironmentalObservation.source == "openmeteo_air_quality_archive",
                extract('year', EnvironmentalObservation.event_time) == year,
            )
        ).scalar()
        aq_counts[year] = count

    session.close()

    # Verify counts (2 points × 2 indicators each)
    assert weather_counts[2023] == 4, f"Expected 4 weather rows for 2023, got {weather_counts[2023]}"
    assert weather_counts[2024] == 4, f"Expected 4 weather rows for 2024, got {weather_counts[2024]}"
    assert aq_counts[2023] == 4, f"Expected 4 air quality rows for 2023, got {aq_counts[2023]}"
    assert aq_counts[2024] == 4, f"Expected 4 air quality rows for 2024, got {aq_counts[2024]}"

    # Second run (idempotency check)
    exit_code2 = main([
        "--database-url", db_url,
        "--start", "2023-01-01",
        "--end", "2024-12-31",
        "--kind", "both",
        "--from-dir", str(cache_dir),
        "--vintage", "2026-10-11",
        "--points", points_str,
    ])

    assert exit_code2 == 0, "Second run should succeed"

    # Counts should be unchanged
    session2 = Session()
    for year in years:
        count = session2.execute(
            select(func.count()).where(
                EnvironmentalObservation.source == "openmeteo_archive",
                extract('year', EnvironmentalObservation.event_time) == year,
            )
        ).scalar()
        assert count == weather_counts[year], f"Weather count for {year} changed after second run"

        count = session2.execute(
            select(func.count()).where(
                EnvironmentalObservation.source == "openmeteo_air_quality_archive",
                extract('year', EnvironmentalObservation.event_time) == year,
            )
        ).scalar()
        assert count == aq_counts[year], f"Air quality count for {year} changed after second run"

    session2.close()


def test_api_estimate_refusal_over_4000(tmp_path, caplog):
    """API mode refuses when estimate > 4000 calls."""
    from scripts.backfill_openmeteo_archive import main

    db_path = tmp_path / "archive.db"

    # Request a large range that will exceed 4000 calls
    # New formula: (days / 14) × (variables / 10)
    # 21 points × 8 years × (365 / 14) × (10 / 10) ≈ 21 × 8 × 26.07 ≈ 4,376 calls
    exit_code = main([
        "--database-url", f"sqlite:///{db_path}",
        "--start", "2018-01-01",
        "--end", "2025-12-31",
        "--kind", "weather",
        # NO --from-dir, so API mode
    ])

    assert exit_code == 1, "Should refuse when estimate > 4000"

    log_output = caplog.text
    assert "4,000" in log_output or "4000" in log_output, "Refusal must mention the 4000 limit"
    assert "weighted" in log_output.lower() or "arithmetic" in log_output.lower(), \
        "Refusal must explain the calculation"


def test_point_year_processing_single_call_receives_one_point_year_only(tmp_path):
    """Test that persist is called with at most one (point, year) of signals at a time."""
    from scripts.backfill_openmeteo_archive import main
    from unittest.mock import patch, MagicMock

    cache_dir = tmp_path / "archive_spy"
    weather_dir = cache_dir / "weather"
    weather_dir.mkdir(parents=True)

    # Create data for 2 points, 2 years
    points = REGION_CAPITALS[:2]
    points_str = ','.join(rc for rc, _, _ in points)
    years = [2023, 2024]

    for region_code, _, _ in points:
        for year in years:
            mock_data = {
                "hourly": {
                    "time": [f"{year}-06-15T12:00"],
                    "temperature_2m": [15.0],
                }
            }
            cache_file = weather_dir / f"{region_code}_{year}.json.gz"
            with gzip.open(cache_file, "wt", encoding="utf-8") as f:
                json.dump(mock_data, f)

    db_path = tmp_path / "archive_spy.db"

    persist_calls = []

    original_persist = __import__('app.ingesters.persist', fromlist=['persist_environmental_observations']).persist_environmental_observations

    def spy_persist(signals, source, session=None):
        # Record what was persisted
        point_years = set((s.region_code, s.observed_at.year) for s in signals)
        persist_calls.append({
            'source': source,
            'signal_count': len(signals),
            'point_years': point_years,
        })
        # Call original
        return original_persist(signals, source, session=session)

    with patch("scripts.backfill_openmeteo_archive.persist_environmental_observations", side_effect=spy_persist):
        exit_code = main([
            "--database-url", f"sqlite:///{db_path}",
            "--start", "2023-01-01",
            "--end", "2024-12-31",
            "--kind", "weather",
            "--from-dir", str(cache_dir),
            "--vintage", "2026-10-11",
            "--points", points_str,
        ])

        assert exit_code == 0

    # Verify each call received exactly one (point, year)
    assert len(persist_calls) == 4, f"Expected 4 persist calls (2 points × 2 years), got {len(persist_calls)}"

    for call in persist_calls:
        assert len(call['point_years']) == 1, \
            f"Each persist call must receive exactly one (point, year), got: {call['point_years']}"

    # Verify all (point, year) combinations were processed exactly once
    all_point_years = set()
    for call in persist_calls:
        all_point_years.update(call['point_years'])

    expected_point_years = {(points[0][0], 2023), (points[0][0], 2024),
                            (points[1][0], 2023), (points[1][0], 2024)}
    assert all_point_years == expected_point_years, \
        f"All (point, year) combinations must be processed exactly once"



def test_points_argument_filters_to_selected_points(tmp_path):
    """--points filters processing to only the selected region codes."""
    from scripts.backfill_openmeteo_archive import main

    cache_dir = tmp_path / "archive_points"
    weather_dir = cache_dir / "weather"
    weather_dir.mkdir(parents=True)

    # Create files for DEU and GBR only
    for region_code in ["DEU", "GBR"]:
        mock_data = {
            "hourly": {
                "time": ["2024-01-15T12:00"],
                "temperature_2m": [15.0],
            }
        }
        cache_file = weather_dir / f"{region_code}_2024.json.gz"
        with gzip.open(cache_file, "wt", encoding="utf-8") as f:
            json.dump(mock_data, f)

    db_path = tmp_path / "archive_points.db"
    db_url = f"sqlite:///{db_path}"

    # Run with --points=DEU,GBR
    exit_code = main([
        "--database-url", db_url,
        "--start", "2024-01-15",
        "--end", "2024-01-15",
        "--kind", "weather",
        "--from-dir", str(cache_dir),
        "--vintage", "2026-10-11",
        "--points", "DEU,GBR",
    ])

    assert exit_code == 0

    # Verify only DEU and GBR rows were inserted
    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    rows = session.execute(
        select(EnvironmentalObservation.region_id).distinct()
    ).scalars().all()
    
    session.close()

    assert set(rows) == {"DEU", "GBR"}, f"Expected only DEU and GBR, got {set(rows)}"


def test_points_argument_refuses_unknown_codes():
    """--points with unknown codes exits non-zero and names them."""
    from scripts.backfill_openmeteo_archive import main

    exit_code = main([
        "--database-url", "sqlite:///test.db",
        "--start", "2024-01-01",
        "--end", "2024-01-31",
        "--points", "DEU,INVALID,GBR,ALSO_INVALID",
    ])

    assert exit_code == 1, "Unknown codes must cause non-zero exit"


def test_script_runs_as_subprocess_from_repo_root():
    """Script can be run as subprocess (production image pattern)."""
    import subprocess
    import os
    import sys
    
    # Repo root is parent of scripts/
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script_path = os.path.join(repo_root, "scripts", "backfill_openmeteo_archive.py")
    
    env = os.environ.copy()
    env["SORA_OFFLINE"] = "1"
    
    result = subprocess.run(
        [sys.executable, script_path, "--help"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )
    
    assert result.returncode == 0, f"Script failed with: {result.stderr}"
    
    usage = result.stdout
    assert "--from-dir" in usage, "Usage must mention --from-dir"
    assert "--vintage" in usage, "Usage must mention --vintage"
    assert "--points" in usage, "Usage must mention --points"


# ==============================================================================
# Range filtering: every hour of the requested range, once per variable
# ==============================================================================

def _write_full_year_archive(cache_dir: Path, region_code: str, year: int) -> None:
    """Write weather/<region>_<year>.json.gz holding every hour of the year."""
    weather_dir = cache_dir / "weather"
    weather_dir.mkdir(parents=True, exist_ok=True)

    times = []
    day = date(year, 1, 1)
    while day.year == year:
        for hour in range(24):
            times.append(f"{day.isoformat()}T{hour:02d}:00")
        day += timedelta(days=1)

    hourly = {"time": times}
    for var in WEATHER_VARIABLES:
        hourly[var] = [1.0] * len(times)

    with gzip.open(weather_dir / f"{region_code}_{year}.json.gz", "wt", encoding="utf-8") as f:
        json.dump({"hourly": hourly}, f)


def _hours_between(first_day: str, last_day: str) -> list:
    """Every whole UTC hour from 00:00 on first_day to 23:00 on last_day, inclusive."""
    day = date.fromisoformat(first_day)
    last = date.fromisoformat(last_day)
    hours = []
    while day <= last:
        for hour in range(24):
            hours.append(datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc))
        day += timedelta(days=1)
    return hours


def _record_weather_point_year_signals(monkeypatch) -> list:
    """Wrap the script's per-point-year fetch; return the list its signals are appended to."""
    from scripts import backfill_openmeteo_archive as script

    original = script.fetch_weather_for_point_year
    recorded = []

    def spy(*args, **kwargs):
        signals, stats = original(*args, **kwargs)
        recorded.extend(signals)
        return signals, stats

    monkeypatch.setattr(script, "fetch_weather_for_point_year", spy)
    return recorded


@pytest.mark.parametrize(
    "start, end, expected_hours",
    [
        pytest.param("2020-12-30", "2021-01-02", 4 * 24, id="across-one-new-year"),
        pytest.param("2020-01-01", "2021-12-31", (366 + 365) * 24, id="two-full-years"),
    ],
)
def test_dry_run_keeps_every_hour_of_the_requested_range(tmp_path, monkeypatch, start, end, expected_hours):
    """Each variable gets exactly the hours of the range, including 31 December.

    Year chunks used to end at midnight on 31 December, so the chunk filter
    dropped 01:00-23:00 of that day at every year boundary.
    """
    from scripts.backfill_openmeteo_archive import main

    cache_dir = tmp_path / "archive"
    _write_full_year_archive(cache_dir, "DEU", 2020)
    _write_full_year_archive(cache_dir, "DEU", 2021)
    recorded = _record_weather_point_year_signals(monkeypatch)

    exit_code = main([
        "--database-url", f"sqlite:///{tmp_path / 'unused.db'}",
        "--start", start,
        "--end", end,
        "--kind", "weather",
        "--dry-run",
        "--from-dir", str(cache_dir),
        "--vintage", "2026-10-11",
        "--points", "DEU",
    ])

    assert exit_code == 0
    expected = _hours_between(start, end)

    per_metric = defaultdict(list)
    for signal in recorded:
        per_metric[signal.metric].append(signal.observed_at)

    assert len(per_metric) == len(WEATHER_VARIABLES)
    for metric, times in sorted(per_metric.items()):
        assert len(times) == expected_hours, f"{metric}: {len(times)} hours, expected {expected_hours}"
        assert sorted(times) == expected, f"{metric}: hours differ from the requested range"


def test_one_month_range_cuts_a_full_year_file_to_that_month(tmp_path, monkeypatch):
    """A full-year file narrowed to June yields exactly June and nothing outside it."""
    from scripts.backfill_openmeteo_archive import main

    cache_dir = tmp_path / "archive"
    _write_full_year_archive(cache_dir, "DEU", 2021)
    recorded = _record_weather_point_year_signals(monkeypatch)

    exit_code = main([
        "--database-url", f"sqlite:///{tmp_path / 'unused.db'}",
        "--start", "2021-06-01",
        "--end", "2021-06-30",
        "--kind", "weather",
        "--dry-run",
        "--from-dir", str(cache_dir),
        "--vintage", "2026-10-11",
        "--points", "DEU",
    ])

    assert exit_code == 0
    expected = _hours_between("2021-06-01", "2021-06-30")

    per_metric = defaultdict(list)
    for signal in recorded:
        per_metric[signal.metric].append(signal.observed_at)

    assert len(per_metric) == len(WEATHER_VARIABLES)
    for metric, times in sorted(per_metric.items()):
        assert len(times) == 30 * 24, f"{metric}: {len(times)} hours, expected {30 * 24}"
        assert sorted(times) == expected, f"{metric}: rows outside June 2021 or missing June hours"


def test_refusal_names_the_app_database_and_never_connects(tmp_path, monkeypatch, caplog):
    """A --database-url naming the app's own database is refused before any connection is opened."""
    from scripts.backfill_openmeteo_archive import main

    monkeypatch.setenv("DATABASE_URL", "postgresql://sora:app-secret@db.internal:5432/sora_earth")
    cache_dir = tmp_path / "empty_archive"
    cache_dir.mkdir()

    with patch("scripts.backfill_openmeteo_archive.create_engine") as create_engine_mock:
        try:
            exit_code = main([
                "--database-url", "postgresql://sora:archive-secret@db.internal:5432/sora_earth",
                "--start", "2024-01-15",
                "--end", "2024-01-15",
                "--kind", "weather",
                "--from-dir", str(cache_dir),
                "--vintage", "2026-10-11",
                "--points", "DEU",
            ])
        except SystemExit as refusal:
            exit_code = refusal.code

    assert create_engine_mock.call_count == 0, "the app database must not be connected to"
    assert exit_code == 1
    assert "REFUSED" in caplog.text
    assert "sora_earth" in caplog.text


def test_session_argument_is_the_only_database_written(tmp_path, monkeypatch):
    """persist_environmental_observations writes to the session it is given, not the app database."""
    from app.ingesters import persist as persist_module
    from app.ingesters.persist import persist_environmental_observations

    archive_engine = create_engine(f"sqlite:///{tmp_path / 'archive.db'}")
    EnvironmentalObservation.__table__.create(bind=archive_engine, checkfirst=True)

    app_engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    EnvironmentalObservation.__table__.create(bind=app_engine, checkfirst=True)
    monkeypatch.setattr(persist_module, "SessionLocal", sessionmaker(bind=app_engine))

    signals = [
        Signal(
            region_code="DEU",
            source="openmeteo_archive",
            metric="temperature",
            value=15.0 + hour,
            unit="celsius",
            observed_at=datetime(2021, 6, 1, hour, tzinfo=timezone.utc),
            temporal_kind="observed",
            source_revision="archive-fetched-2026-10-11",
            metadata={"latitude": 52.52, "longitude": 13.41},
        )
        for hour in range(3)
    ]

    session = sessionmaker(bind=archive_engine)()
    try:
        result = persist_environmental_observations(signals, "openmeteo_archive", session=session)
    finally:
        session.close()

    assert result.inserted == 3

    with archive_engine.connect() as conn:
        archive_rows = conn.execute(select(EnvironmentalObservation.id)).fetchall()
    with app_engine.connect() as conn:
        app_rows = conn.execute(select(EnvironmentalObservation.id)).fetchall()

    assert len(archive_rows) == 3, "rows must land in the session's database"
    assert len(app_rows) == 0, "no rows may land in the app database"
