"""The expected grid's declared points, and what happens outside them.

Runs without PostgreSQL. The SQL is covered in test_observation_coverage_postgres.py;
these pin what the SQL is given: the registry, the refusal for a pair nothing
declares, the route's 422, and the scheduler's behaviour when it cannot report.
"""
import ast
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: The points the Open-Meteo ingester fetched before the registry existed, in
#: that order. The registry move must not change this list.
FETCHED_BEFORE_THE_REGISTRY = [
    "DEU", "GBR", "FRA", "ESP", "ITA", "NLD", "POL",
    "IND", "CHN", "JPN", "KOR", "IDN",
    "USA", "BRA", "MEX", "CAN",
    "NGA", "ZAF", "KEN",
    "RU-MOW", "RU-SPE",
]


def test_the_registry_is_the_points_the_ingester_fetches_in_order():
    from app.ingesters.openmeteo import REGION_CAPITALS
    from app.services.environmental.regions import OPENMETEO_POINTS_V1

    assert [p.code for p in OPENMETEO_POINTS_V1] == FETCHED_BEFORE_THE_REGISTRY
    assert [code for code, _lat, _lon in REGION_CAPITALS] == FETCHED_BEFORE_THE_REGISTRY
    assert REGION_CAPITALS == [(p.code, p.lat, p.lon) for p in OPENMETEO_POINTS_V1]


def test_every_point_is_due_from_the_declared_start():
    from app.services.environmental.regions import OPENMETEO_POINTS_V1, OPENMETEO_SINCE

    assert OPENMETEO_SINCE == date(2026, 7, 30)
    assert {p.since for p in OPENMETEO_POINTS_V1} == {OPENMETEO_SINCE}


def test_only_the_measured_pair_is_declared():
    """Other Open-Meteo variables are not confirmed to arrive for every point, so
    declaring them now would turn partial fetches into gaps."""
    from app.services.environmental.regions import COVERAGE_REGISTRY

    assert set(COVERAGE_REGISTRY) == {("openmeteo", "temperature")}


@pytest.mark.parametrize("source,indicator", [
    ("openaq", "temperature"),
    ("openmeteo", "humidity"),
])
def test_an_undeclared_pair_is_refused_rather_than_counted(source, indicator):
    from app.services.observation_coverage import CoverageNotSupported, coverage_report

    with pytest.raises(CoverageNotSupported, match="not supported"):
        coverage_report(source=source, indicator=indicator, days=30)


def test_the_route_answers_422_for_an_undeclared_source():
    from fastapi import HTTPException

    import app.api.infra as infra

    with pytest.raises(HTTPException) as info:
        infra.observation_coverage(
            _admin=None, source="openaq", indicator="temperature", days=30)

    assert info.value.status_code == 422
    assert "not supported" in info.value.detail


def test_the_registry_module_imports_only_the_standard_library():
    """The scheduler runs coverage without the API's auth configuration. The
    registry sits under that import, so it may not reach app.api or app.auth."""
    tree = ast.parse((REPO / "app" / "services" / "environmental" / "regions.py")
                     .read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")

    assert imported <= {"dataclasses", "datetime"}, imported


def test_the_scheduler_does_not_publish_for_an_undeclared_pair(monkeypatch, caplog):
    """A scheduler pointed at an undeclared pair must leave the gauges unset and
    say so: a zero published for it would look like a healthy window."""
    import app.scheduler as scheduler_module
    from prometheus_client import REGISTRY

    monkeypatch.setattr(scheduler_module, "COVERAGE_WATCH", ("openaq", "temperature"))

    with caplog.at_level("WARNING"):
        result = scheduler_module.scheduled_observation_coverage()

    assert result["status"] == "error"
    assert "not supported" in result["error"]
    labels = {"source": "openaq", "indicator": "temperature"}
    assert REGISTRY.get_sample_value(
        "sora_observation_coverage_gap_days", labels) is None
    assert REGISTRY.get_sample_value(
        "sora_observation_coverage_last_success_timestamp_seconds", labels) is None
    assert any("Observation coverage check failed" in r.getMessage()
               for r in caplog.records)


def test_utc_today_is_a_function_a_test_can_state_the_calendar_with():
    import app.services.observation_coverage as coverage

    assert isinstance(coverage.utc_today(), date)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """Stands in for the database: returns the grid rows the SQL would return.

    The SQL's job is the grid itself; the threshold comparison is the Python
    filter in coverage_report. This lets that filter run without PostgreSQL.
    """

    def __init__(self, rows):
        self._rows = rows

    def execute(self, statement, params=None):
        return _FakeResult(self._rows)

    def close(self):
        pass


def test_the_threshold_boundary_is_exact_without_a_database(monkeypatch):
    """19 hours of 24 is complete; 18 is a gap. The comparison is `<`, not `<=`.

    The Postgres twin of this lives in test_observation_coverage_postgres.py. This
    one runs anywhere: the grid rows come from a fake session, and the comparison
    under test is coverage_report's own filter.
    """
    from datetime import timedelta

    import app.database as database
    from app.services.environmental.regions import OPENMETEO_POINTS_V1
    from app.services.observation_coverage import coverage_report

    today = date(2026, 10, 20)
    nineteen_day = today - timedelta(days=2)
    eighteen_day = today - timedelta(days=4)
    rows = []
    for back in range(7, 0, -1):
        day = today - timedelta(days=back)
        for point in OPENMETEO_POINTS_V1:
            hours = 24
            if day == nineteen_day and point.code == "RU-MOW":
                hours = 19
            if day == eighteen_day and point.code == "RU-SPE":
                hours = 18
            rows.append((day.isoformat(), point.code, hours))

    monkeypatch.setattr(database, "SessionLocal", lambda: _FakeSession(rows))

    result = coverage_report(source="openmeteo", indicator="temperature",
                             days=7, today=today)

    assert [(g.day, g.region_id, g.observations) for g in result.gaps] == [
        (eighteen_day.isoformat(), "RU-SPE", 18),
    ]
    assert (result.gap_count, result.complete_days) == (1, 146)
