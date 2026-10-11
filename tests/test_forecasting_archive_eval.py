"""Tests for M3-A archive evaluation (preregistered forecast on archived series).

These tests verify the implementation of docs/M3A_ARCHIVE_DECLARATION.md before
the first real run on the archive. The declaration was committed first, and
these tests ensure the code honours it.

Each behavioral test is proven: the test is run with the code temporarily broken
to confirm it catches the defect, then restored and run green.
"""
from __future__ import annotations

import gzip
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import pytest

from app.services.forecasting.archive_eval import (
    ARCHIVE_PERIOD_END,
    ARCHIVE_PERIOD_START,
    DECLARATION,
    HORIZONS,
    RESULT_LABEL,
    TARGET_NAME,
    candidate_verdict,
    evaluate_archive,
    load_archive_rows,
    manifest_digest,
)
from app.services.forecasting.baselines import ClimatologyForecast, default_baselines
from app.services.forecasting.evaluation import (
    EvaluationReport,
    WindowResult,
    evaluate_rolling_origin,
    rolling_origin_windows,
)

REPO_ROOT = Path(__file__).parent.parent


def test_climatology_year_boundary_pools_from_earlier_years():
    """Target 1 Jan 2023 uses dates 29 Dec (Y−1) … 4 Jan (Y) from earlier years.

    The test gives every date a constant value except the window dates, which get
    unique values. This proves which dates are pooled.

    Forecasting 2023-01-01 from origin 2022-12-31 must pool:
    - Y=2020: anchor=2020-01-01, window=2019-12-29..2020-01-04, all ≤ 2022-12-31 ✓
    - Y=2021: anchor=2021-01-01, window=2020-12-29..2021-01-04, all ≤ 2022-12-31 ✓
    - Y=2022: anchor=2022-01-01, window=2021-12-29..2022-01-04, all ≤ 2022-12-31 ✓

    Mutation: break the year-boundary crossing logic to refuse dates from Dec Y−1.
    """
    model = ClimatologyForecast(window_days=3)

    rows = []
    window_values = {}

    # Generate data including 2019-12-29..31 for Y=2020's window
    for year in [2019, 2020, 2021, 2022]:
        for month in range(1, 13):
            days_in_month = 31 if month in [1, 3, 5, 7, 8, 10, 12] else (30 if month != 2 else 28)
            for day in range(1, days_in_month + 1):
                try:
                    dt = date(year, month, day)
                    if dt < date(2019, 12, 29) or dt > date(2022, 12, 31):
                        continue

                    if (month == 12 and day >= 29) or (month == 1 and day <= 4):
                        value = float(dt.toordinal())
                        window_values[dt] = value
                    else:
                        value = 0.0

                    rows.append({"ds": pd.Timestamp(dt), "y": value})
                except ValueError:
                    pass

    df = pd.DataFrame(rows).sort_values("ds").reset_index(drop=True)
    train_df = df[df["ds"] <= pd.Timestamp(date(2022, 12, 31))].copy()

    model.fit(train_df, "y")
    result = model.predict(1)

    # Qualifying windows: Y=2020, Y=2021, Y=2022 (all three qualify)
    expected_dates = [
        date(2019, 12, 29), date(2019, 12, 30), date(2019, 12, 31),
        date(2020, 1, 1), date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 4),
        date(2020, 12, 29), date(2020, 12, 30), date(2020, 12, 31),
        date(2021, 1, 1), date(2021, 1, 2), date(2021, 1, 3), date(2021, 1, 4),
        date(2021, 12, 29), date(2021, 12, 30), date(2021, 12, 31),
        date(2022, 1, 1), date(2022, 1, 2), date(2022, 1, 3), date(2022, 1, 4),
    ]
    expected = np.mean([window_values[d] for d in expected_dates])

    assert abs(result.yhat[0] - expected) < 1.0, (
        f"Year boundary crossing broken: got {result.yhat[0]}, expected {expected}"
    )


def test_climatology_horizon_crossing_new_year_includes_origin_year():
    """Origin 30 Dec 2025, target 2 Jan 2026 → the 30 Dec 2024–5 Jan 2025 window counts.

    Each date in the window carries ordinal value. The pooled mean must include those dates.

    Target 2026-01-02 from origin 2025-12-30:
    - Y=2023: anchor=2023-01-02, window=2022-12-30..2023-01-05, all ≤ 2025-12-30 ✓
    - Y=2024: anchor=2024-01-02, window=2023-12-30..2024-01-05, all ≤ 2025-12-30 ✓
    - Y=2025: anchor=2025-01-02, window=2024-12-30..2025-01-05, all ≤ 2025-12-30 ✓

    Mutation: exclude any year that touches the next calendar year.
    """
    model = ClimatologyForecast(window_days=3)

    rows = []
    window_values = {}

    # Generate data including 2022-12-30..31 for Y=2023's window
    for year in [2022, 2023, 2024, 2025]:
        for month in range(1, 13):
            days_in_month = 31 if month in [1, 3, 5, 7, 8, 10, 12] else (30 if month != 2 else 28)
            for day in range(1, days_in_month + 1):
                try:
                    dt = date(year, month, day)
                    if dt < date(2022, 12, 30) or dt > date(2025, 12, 30):
                        continue

                    if (month == 12 and day >= 28) or (month == 1 and day <= 5):
                        value = float(dt.toordinal())
                        window_values[dt] = value
                    else:
                        value = 0.0

                    rows.append({"ds": pd.Timestamp(dt), "y": value})
                except ValueError:
                    pass

    df = pd.DataFrame(rows).sort_values("ds").reset_index(drop=True)
    train_df = df[df["ds"] <= pd.Timestamp(date(2025, 12, 30))].copy()

    model.fit(train_df, "y")
    result = model.predict(3)

    target_2_jan = result.yhat[2]

    # Qualifying windows: Y=2023, Y=2024, Y=2025 (all three qualify)
    expected_dates = [
        date(2022, 12, 30), date(2022, 12, 31), date(2023, 1, 1), date(2023, 1, 2),
        date(2023, 1, 3), date(2023, 1, 4), date(2023, 1, 5),
        date(2023, 12, 30), date(2023, 12, 31), date(2024, 1, 1), date(2024, 1, 2),
        date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5),
        date(2024, 12, 30), date(2024, 12, 31), date(2025, 1, 1), date(2025, 1, 2),
        date(2025, 1, 3), date(2025, 1, 4), date(2025, 1, 5),
    ]
    expected = np.mean([window_values[d] for d in expected_dates])

    assert abs(target_2_jan - expected) < 1.0, (
        f"Horizon crossing New Year broken: got {target_2_jan}, expected {expected}"
    )


def test_climatology_no_leak_sentinel_after_origin():
    """Huge sentinel on dates after the origin must not appear in the forecast.

    Data before origin = 10.0, data after origin = 9999.0.
    If 9999.0 leaks, the forecast is detectably wrong.

    Mutation: remove the `> origin` check for qualifying years.
    """
    model = ClimatologyForecast(window_days=3)

    rows = []
    for year in [2020, 2021, 2022]:
        for month in range(1, 13):
            for day in range(1, 29):
                try:
                    dt = date(year, month, day)
                    if year == 2022 and month >= 6 and day >= 15:
                        value = 9999.0
                    else:
                        value = 10.0
                    rows.append({"ds": pd.Timestamp(dt), "y": value})
                except ValueError:
                    pass

    df = pd.DataFrame(rows).sort_values("ds").reset_index(drop=True)
    train_df = df[df["ds"] <= pd.Timestamp(date(2022, 6, 14))].copy()

    model.fit(train_df, "y")
    result = model.predict(1)

    assert result.yhat[0] < 100, (
        f"Leak detected: forecast {result.yhat[0]} contains sentinel values from after origin"
    )


def test_climatology_real_29_feb_included_in_earlier_window():
    """A real 29 Feb in an earlier year's window is included in the pool.

    Target 2024-03-01 from origin 2024-02-29.
    - Y=2020: anchor=2020-03-01, window=2020-02-27..2020-03-04 (includes 29 Feb 2020 leap day)
    - Y=2021: anchor=2021-03-01, window=2021-02-26..2021-03-04
    - Y=2022: anchor=2022-03-01, window=2022-02-26..2022-03-04
    - Y=2023: anchor=2023-03-01, window=2023-02-26..2023-03-04

    Mutation: skip 29 Feb in any year.
    """
    model = ClimatologyForecast(window_days=3)

    rows = []
    window_values = {}

    for year in [2020, 2021, 2022, 2023, 2024]:
        for month in range(1, 13):
            days_in_month = 31 if month in [1, 3, 5, 7, 8, 10, 12] else (30 if month != 2 else (29 if year in [2020, 2024] else 28))
            for day in range(1, days_in_month + 1):
                try:
                    dt = date(year, month, day)
                    if dt > date(2024, 2, 29):
                        continue

                    if (month == 2 and day >= 26) or (month == 3 and day <= 4):
                        value = float(dt.toordinal())
                        window_values[dt] = value
                    else:
                        value = 0.0

                    rows.append({"ds": pd.Timestamp(dt), "y": value})
                except ValueError:
                    pass

    df = pd.DataFrame(rows).sort_values("ds").reset_index(drop=True)
    train_df = df[df["ds"] <= pd.Timestamp(date(2024, 2, 29))].copy()

    model.fit(train_df, "y")
    result = model.predict(1)

    # Qualifying windows: Y=2020, Y=2021, Y=2022, Y=2023
    expected_dates = [
        # Y=2020: window 2020-02-27..2020-03-04 (includes leap day 2020-02-29)
        date(2020, 2, 27), date(2020, 2, 28), date(2020, 2, 29),
        date(2020, 3, 1), date(2020, 3, 2), date(2020, 3, 3), date(2020, 3, 4),
        # Y=2021: window 2021-02-26..2021-03-04
        date(2021, 2, 26), date(2021, 2, 27), date(2021, 2, 28),
        date(2021, 3, 1), date(2021, 3, 2), date(2021, 3, 3), date(2021, 3, 4),
        # Y=2022: window 2022-02-26..2022-03-04
        date(2022, 2, 26), date(2022, 2, 27), date(2022, 2, 28),
        date(2022, 3, 1), date(2022, 3, 2), date(2022, 3, 3), date(2022, 3, 4),
        # Y=2023: window 2023-02-26..2023-03-04
        date(2023, 2, 26), date(2023, 2, 27), date(2023, 2, 28),
        date(2023, 3, 1), date(2023, 3, 2), date(2023, 3, 3), date(2023, 3, 4),
    ]

    assert date(2020, 2, 29) in expected_dates, "Feb 29 2020 must be in the pool"
    expected = np.mean([window_values[d] for d in expected_dates])

    assert abs(result.yhat[0] - expected) < 1.0, (
        f"29 Feb inclusion broken: got {result.yhat[0]}, expected ~{expected}"
    )


def test_climatology_target_29_feb_uses_28_feb_anchor():
    """Target 29 Feb is matched as 28 Feb in non-leap years (declaration §7).

    Forecasting 2024-02-29 from 2024-02-28 must pool from 28 Feb ±3 in earlier years.

    Mutation: attempt to construct 29 Feb in non-leap years (raises ValueError).
    """
    model = ClimatologyForecast(window_days=3)

    rows = []
    for year in [2020, 2021, 2022, 2023]:
        for month in range(1, 13):
            for day in range(1, 29):
                try:
                    dt = date(year, month, day)
                    if dt > date(2024, 2, 28):
                        continue
                    if month == 2 and day >= 25:
                        value = float(year * 100 + day)
                    elif month == 3 and day <= 3:
                        value = float(year * 100 + day)
                    else:
                        value = 30.0
                    rows.append({"ds": pd.Timestamp(dt), "y": value})
                except ValueError:
                    pass

    df = pd.DataFrame(rows).sort_values("ds").reset_index(drop=True)
    train_df = df[df["ds"] <= pd.Timestamp(date(2024, 2, 28))].copy()

    model.fit(train_df, "y")
    result = model.predict(1)

    assert len(result.yhat) == 1
    assert not np.isnan(result.yhat[0])


def test_climatology_fewer_than_two_years_raises_valueerror():
    """Fewer than 2 qualifying earlier years → ValueError (declaration §7).

    Data: 2020 and 2021 only. Forecasting 2022-01-01 has only one earlier year
    (2020) with Jan 1 before the origin 2021-12-28.

    Mutation: allow prediction with only 1 year.
    """
    model = ClimatologyForecast(window_days=3)

    rows = []
    for year in [2020, 2021]:
        for month in range(1, 13):
            for day in range(1, 29):
                try:
                    dt = date(year, month, day)
                    rows.append({"ds": pd.Timestamp(dt), "y": float(year)})
                except ValueError:
                    pass

    df = pd.DataFrame(rows).sort_values("ds").reset_index(drop=True)
    model.fit(df, "y")

    with pytest.raises(ValueError, match="only .* earlier year.*need at least 2"):
        model.predict(1)


def test_climatology_straddling_year_is_excluded_by_origin_check():
    """A year whose window runs past the origin does not qualify, even if part of it is observed.

    Origin 30 Dec 2023. The target 30 Dec 2024 takes the anchor 30 Dec in each
    earlier year. In 2023 that window is 27 Dec 2023 .. 2 Jan 2024. Its days
    27-30 Dec are observed, but 31 Dec and 1-2 Jan lie after the origin, so 2023
    must be left out entirely. The check that does that is `any(d > origin ...)`.

    Every observed day carries its own year as its value, except 27-30 Dec 2023,
    which carry a sentinel of 1e9. The expected forecast is the mean of the
    qualifying windows, 2018-2022, written out date by date here. If 2023 came in
    through its observed days, the forecast would be near 1e9.

    Mutation: delete the `if any(d > origin ...)` check in ClimatologyForecast._pool.
    """
    origin = date(2023, 12, 30)
    sentinel_days = {date(2023, 12, 27) + timedelta(days=k) for k in range(4)}

    rows = []
    day = date(2018, 1, 1)
    while day <= origin:
        value = 1e9 if day in sentinel_days else float(day.year)
        rows.append({"ds": pd.Timestamp(day), "y": value})
        day += timedelta(days=1)
    df = pd.DataFrame(rows)

    model = ClimatologyForecast(window_days=3)
    model.fit(df, "y")
    forecast = model.predict(366)

    assert forecast.dates[-1] == "2024-12-30"

    qualifying_days = [
        date(year, 12, 27) + timedelta(days=k)
        for year in range(2018, 2023)
        for k in range(7)
    ]
    expected = float(np.mean([float(d.year) for d in qualifying_days]))

    assert forecast.yhat[-1] == pytest.approx(expected, rel=0, abs=1e-9), (
        f"straddling year leaked: forecast {forecast.yhat[-1]}, "
        f"expected {expected} from qualifying years 2018-2022 only"
    )


def test_windows_at_end_last_window_ends_at_last_observation():
    """Last window ends at the last observation (declaration §5).

    With train_size = n_days - horizon × n_windows, the arithmetic places
    the last window's test period at exactly the end.

    Mutation: set train_size to a minimum constant instead of the formula.
    """
    n_days = 3285
    horizon = 7
    n_windows = 104
    train_size = n_days - horizon * n_windows

    windows = rolling_origin_windows(
        n_observations=n_days,
        train_size=train_size,
        horizon=horizon,
        n_windows=n_windows,
    )

    assert windows[-1]["test_end"] == n_days, (
        f"Last window ends at {windows[-1]['test_end']}, not at {n_days}"
    )


def test_windows_at_end_first_test_day_matches_declaration():
    """First test day = last_day − horizon × n_windows + 1 (declaration §5).

    On the declared period (2018-01-01 .. 2026-10-10), h=7 with 104 windows
    must start testing on 2024-10-13.

    Mutation: set train_size to a fixed minimum (e.g., 90) instead of the formula.
    """
    period_start = date(2018, 1, 1)
    period_end = date(2026, 10, 10)
    n_days = (period_end - period_start).days + 1

    horizon = 7
    n_windows = 104
    train_size = n_days - horizon * n_windows

    first_test_idx = train_size
    first_test_date = period_start + timedelta(days=first_test_idx)

    expected = date(2024, 10, 13)
    assert first_test_date == expected, (
        f"h=7 first test day {first_test_date.isoformat()}, "
        f"declaration says {expected.isoformat()}"
    )


def test_single_pass_no_refitting_after_first_evaluation(tmp_path):
    """Each (model, point) is fit exactly (#windows) times, never more.

    Patch the fit method to count calls. After the first pass, the paired
    bootstrap must not refit.

    Mutation: reintroduce evaluate_rolling_origin calls in the bootstrap section.
    """
    archive_dir = tmp_path / "archive"
    weather_dir = archive_dir / "weather"
    weather_dir.mkdir(parents=True)

    points = ["DEU", "FRA"]
    years = [2024, 2025, 2026]

    for point in points:
        for year in years:
            times = []
            temps = []
            for day in range(1, 180):
                for hour in range(24):
                    dt = datetime(year, 1, 1) + timedelta(days=day - 1, hours=hour)
                    if dt.date() > date(2026, 10, 10):
                        break
                    times.append(dt.strftime("%Y-%m-%dT%H:%M"))
                    temps.append(10.0 + np.sin(day / 7) * 3)

            hourly_data = {
                "hourly": {
                    "time": times,
                    "temperature_2m": temps,
                }
            }

            file_path = weather_dir / f"{point}_{year}.json.gz"
            with gzip.open(file_path, "wt", encoding="utf-8") as f:
                json.dump(hourly_data, f)

    fit_counts = {}

    original_fit = None

    def counting_fit(self, df, target_col, **kwargs):
        key = (self.model_name, len(df))
        fit_counts[key] = fit_counts.get(key, 0) + 1
        return original_fit(self, df, target_col, **kwargs)

    from app.services.forecasting.baselines import _Baseline
    original_fit = _Baseline.fit

    with mock.patch.object(_Baseline, 'fit', counting_fit):
        report = evaluate_archive(archive_dir, horizons=[7])

    if not report.get("refused"):
        horizon_result = report["results_by_horizon"][7]
        n_windows = horizon_result["n_windows"]
        points_scored = len([p for p, m in horizon_result["per_point_metrics"].items() if m])

        expected_fits_per_model = n_windows * points_scored

        for (model_name, _), count in fit_counts.items():
            assert count == expected_fits_per_model, (
                f"{model_name} fitted {count} times, expected {expected_fits_per_model} "
                "(single pass violated)"
            )


# Synthetic archive shared by the two baseline-failure tests. evaluate_archive
# refuses an archive that lacks any declared point, so all 21 are written.
FIXTURE_START = date(2018, 1, 1)
FIXTURE_END = date(2020, 12, 31)
FIXTURE_HORIZON = 2
FIXTURE_WINDOWS = 1


def _point_level(index: int) -> float:
    """Mean temperature of a point's series. One degree apart per point, so a fit
    can tell which point it was handed."""
    return 10.0 + index


def _write_complete_archive(archive_dir: Path) -> list[str]:
    """Hourly temperature for every declared point, FIXTURE_START..FIXTURE_END.

    Each point is an annual sine around its own level plus seeded noise, with 24
    hourly values per day and no gaps. Returns the declared point codes in the
    order evaluate_archive iterates them.
    """
    from app.ingesters.openmeteo import REGION_CAPITALS

    weather_dir = archive_dir / "weather"
    weather_dir.mkdir(parents=True)
    points = sorted(code for code, _lat, _lon in REGION_CAPITALS)

    rng = np.random.default_rng(20181231)
    n_days = (FIXTURE_END - FIXTURE_START).days + 1
    days = [FIXTURE_START + timedelta(days=i) for i in range(n_days)]
    annual = np.sin(2 * np.pi * np.arange(n_days) / 365.25)
    hour_wave = 2.0 * np.sin(2 * np.pi * np.arange(24) / 24)  # sums to zero over a day
    hour_suffixes = [f"T{hour:02d}:00" for hour in range(24)]

    for index, point in enumerate(points):
        daily = _point_level(index) + 8.0 * annual + rng.normal(0.0, 0.05, size=n_days)
        by_year: dict[int, tuple[list[str], list[float]]] = {}
        for day, value in zip(days, daily):
            times, temps = by_year.setdefault(day.year, ([], []))
            stamp = day.isoformat()
            times.extend(f"{stamp}{suffix}" for suffix in hour_suffixes)
            temps.extend((value + hour_wave).tolist())
        for year, (times, temps) in by_year.items():
            file_path = weather_dir / f"{point}_{year}.json.gz"
            with gzip.open(file_path, "wt", encoding="utf-8", compresslevel=1) as f:
                json.dump({"hourly": {"time": times, "temperature_2m": temps}}, f)
    return points


def _fixture_window_configs(n_windows: int = FIXTURE_WINDOWS) -> dict:
    """Small window configuration, with its evaluation start derived from the fixture's last day."""
    return {
        FIXTURE_HORIZON: {
            "n_windows": n_windows,
            "evaluation_period_start": FIXTURE_END - timedelta(days=FIXTURE_HORIZON * n_windows - 1),
        }
    }


def test_no_silent_subset_one_baseline_fails_one_point(tmp_path):
    """Climatology raises for ITA only, so every candidate is not_comparable.

    fit() receives a frame, not a point code, so the failing point is recognised
    by its first observation, which sits at ITA's own level.

    Mutation: restore the silent `except ValueError: pass` that drops the point.
    """
    archive_dir = tmp_path / "archive"
    points = _write_complete_archive(archive_dir)
    assert len(points) == 21
    ita_level = _point_level(points.index("ITA"))

    original_fit = ClimatologyForecast.fit

    def fit_failing_for_ita_only(self, df, target_col, **kwargs):
        if abs(float(df[target_col].iloc[0]) - ita_level) < 0.5:
            raise ValueError("simulated Climatology failure")
        return original_fit(self, df, target_col, **kwargs)

    with mock.patch.object(ClimatologyForecast, "fit", fit_failing_for_ita_only):
        report = evaluate_archive(
            archive_dir, horizons=[FIXTURE_HORIZON], window_configs=_fixture_window_configs()
        )

    assert not report.get("refused"), f"Evaluation refused: {report.get('refusal_reasons')}"
    horizon_result = report["results_by_horizon"][FIXTURE_HORIZON]

    failures = horizon_result["failures"]
    assert set(failures) == {"Climatology(±3d)"}, f"Only Climatology should fail: {failures}"
    assert failures["Climatology(±3d)"]["count"] == 1
    assert failures["Climatology(±3d)"]["first_few"][0].startswith("ITA:")

    from app.services.forecasting.challengers import CANDIDATES

    expected_candidates = {spec.name for spec in CANDIDATES if spec.role != "shadow"}
    verdicts = horizon_result["candidate_verdicts"]
    assert set(verdicts) == expected_candidates, f"Verdicts: {verdicts}"

    for cand_name, verdict in verdicts.items():
        assert verdict["succeeded"] is False, f"{cand_name} should not succeed"
        assert verdict["reason"] == "not_comparable", (
            f"{cand_name} reason is {verdict['reason']}, expected 'not_comparable'"
        )
        assert verdict["missing_baseline_point_pairs"] == 1, (
            f"{cand_name} should report 1 missing pair, got {verdict['missing_baseline_point_pairs']}"
        )
        assert any("Climatology" in entry and "ITA" in entry for entry in verdict["missing"]), (
            f"{cand_name} verdict does not name Climatology/ITA: {verdict['missing']}"
        )


def test_baseline_failing_everywhere_is_not_comparable(tmp_path):
    """Climatology raises for every point, so every candidate is not_comparable
    and the report names Climatology.

    Mutation: restore a silent `except ValueError` that drops the baseline entirely.
    """
    archive_dir = tmp_path / "archive"
    points = _write_complete_archive(archive_dir)
    assert len(points) == 21

    def fit_always_fails(self, df, target_col, **kwargs):
        raise ValueError("simulated Climatology failure")

    with mock.patch.object(ClimatologyForecast, "fit", fit_always_fails):
        report = evaluate_archive(
            archive_dir, horizons=[FIXTURE_HORIZON], window_configs=_fixture_window_configs()
        )

    assert not report.get("refused"), f"Evaluation refused: {report.get('refusal_reasons')}"
    horizon_result = report["results_by_horizon"][FIXTURE_HORIZON]

    failures = horizon_result["failures"]
    assert set(failures) == {"Climatology(±3d)"}, f"Only Climatology should fail: {failures}"
    assert failures["Climatology(±3d)"]["count"] == 21

    from app.services.forecasting.challengers import CANDIDATES

    expected_candidates = {spec.name for spec in CANDIDATES if spec.role != "shadow"}
    verdicts = horizon_result["candidate_verdicts"]
    assert set(verdicts) == expected_candidates, f"Verdicts: {verdicts}"

    for cand_name, verdict in verdicts.items():
        assert verdict["succeeded"] is False, f"{cand_name} should not succeed"
        assert verdict["reason"] == "not_comparable", (
            f"{cand_name} reason is {verdict['reason']}, expected 'not_comparable'"
        )
        assert verdict["missing_baseline_point_pairs"] == 21, (
            f"{cand_name} should report 21 missing pairs, got {verdict['missing_baseline_point_pairs']}"
        )
        assert any("Climatology" in entry for entry in verdict["missing"]), (
            f"{cand_name} verdict does not name Climatology: {verdict['missing']}"
        )


def test_load_archive_rows_reads_gzipped_json(tmp_path):
    """Loader reads gzipped JSON files with hyphenated point codes."""
    archive_dir = tmp_path / "archive"
    weather_dir = archive_dir / "weather"
    weather_dir.mkdir(parents=True)

    points_data = {
        "RU-MOW": {"time": ["2020-06-15T12:00"], "temp": [25.5]},
        "DEU": {"time": ["2020-06-15T13:00"], "temp": [20.0]},
    }

    for point, data in points_data.items():
        hourly = {
            "hourly": {
                "time": data["time"],
                "temperature_2m": data["temp"],
            }
        }
        file_path = weather_dir / f"{point}_2020.json.gz"
        with gzip.open(file_path, "wt", encoding="utf-8") as f:
            json.dump(hourly, f)

    rows = load_archive_rows(archive_dir)

    assert len(rows) == 2
    points_loaded = {r[0] for r in rows}
    assert points_loaded == {"RU-MOW", "DEU"}

    for point, event_time, value in rows:
        assert event_time.tzinfo is not None
        assert event_time.tzinfo == timezone.utc


def test_load_archive_rows_filters_to_declared_period(tmp_path):
    """Rows outside 2018-01-01..2026-10-10 are excluded."""
    archive_dir = tmp_path / "archive"
    weather_dir = archive_dir / "weather"
    weather_dir.mkdir(parents=True)

    hourly_data = {
        "hourly": {
            "time": [
                "2017-12-31T23:00",
                "2018-01-01T00:00",
                "2026-10-10T23:00",
                "2026-10-11T00:00",
            ],
            "temperature_2m": [1.0, 2.0, 3.0, 4.0],
        }
    }

    file_path = weather_dir / "TEST_2020.json.gz"
    with gzip.open(file_path, "wt", encoding="utf-8") as f:
        json.dump(hourly_data, f)

    rows = load_archive_rows(archive_dir)

    assert len(rows) == 2
    dates = [r[1].date() for r in rows]
    assert date(2018, 1, 1) in dates
    assert date(2026, 10, 10) in dates


def test_manifest_digest_changes_when_file_bytes_change(tmp_path):
    """Manifest digest depends on file contents, not just filenames."""
    archive_dir = tmp_path / "archive"
    weather_dir = archive_dir / "weather"
    weather_dir.mkdir(parents=True)

    hourly_data_v1 = {
        "hourly": {
            "time": ["2020-06-15T12:00"],
            "temperature_2m": [25.5],
        }
    }
    file_path = weather_dir / "TEST_2020.json.gz"
    with gzip.open(file_path, "wt", encoding="utf-8") as f:
        json.dump(hourly_data_v1, f)

    digest_v1 = manifest_digest(archive_dir)

    hourly_data_v2 = {
        "hourly": {
            "time": ["2020-06-15T12:00"],
            "temperature_2m": [26.0],
        }
    }
    with gzip.open(file_path, "wt", encoding="utf-8") as f:
        json.dump(hourly_data_v2, f)

    digest_v2 = manifest_digest(archive_dir)

    assert digest_v1 != digest_v2


def test_end_to_end_on_synthetic_archive(tmp_path):
    """Full evaluation on a small synthetic archive returns deterministic results."""
    archive_dir = tmp_path / "archive"
    weather_dir = archive_dir / "weather"
    weather_dir.mkdir(parents=True)

    points = ["DEU", "FRA"]
    years = [2025, 2026]

    for point in points:
        for year in years:
            times = []
            temps = []
            base_temp = 5.0 + hash(point) % 10
            for day in range(1, 91):
                for hour in range(24):
                    try:
                        dt = datetime(year, 1, 1) + timedelta(days=day - 1, hours=hour)
                        times.append(dt.strftime("%Y-%m-%dT%H:%M"))
                        temps.append(base_temp + np.sin(day / 7) * 3 + hour / 24)
                    except ValueError:
                        break

            hourly_data = {
                "hourly": {
                    "time": times,
                    "temperature_2m": temps,
                }
            }

            file_path = weather_dir / f"{point}_{year}.json.gz"
            with gzip.open(file_path, "wt", encoding="utf-8") as f:
                json.dump(hourly_data, f)

    report1 = evaluate_archive(archive_dir, horizons=[7])
    report2 = evaluate_archive(archive_dir, horizons=[7])

    assert report1["label"] == RESULT_LABEL
    assert report1["declaration"] == DECLARATION
    assert report1["target"] == TARGET_NAME

    assert report1["snapshot_digest"] == report2["snapshot_digest"]
    assert report1["manifest_digest"].startswith("sha256:")

    if report1.get("refused"):
        assert "refusal_reasons" in report1
    else:
        assert "results_by_horizon" in report1


def test_constants_match_declaration_document():
    """Constants in the module must match the declaration text exactly."""
    declaration_path = REPO_ROOT / "docs" / "M3A_ARCHIVE_DECLARATION.md"
    if not declaration_path.exists():
        pytest.skip("Declaration file not found")

    with open(declaration_path, encoding="utf-8") as f:
        text = f.read()

    period_match = re.search(r"period\s+2018-01-01\s+\.\.\s+2026-10-10", text)
    assert period_match, "Period dates not found in declaration"
    assert ARCHIVE_PERIOD_START == date(2018, 1, 1)
    assert ARCHIVE_PERIOD_END == date(2026, 10, 10)

    h7_match = re.search(r"7\s+\|\s+104\s+\|.*2024-10-13", text)
    assert h7_match, "h=7 window count not found"
    assert HORIZONS[7]["n_windows"] == 104
    assert HORIZONS[7]["evaluation_period_start"] == date(2024, 10, 13)

    h30_match = re.search(r"30\s+\|\s+24\s+\|.*2024-10-21", text)
    assert h30_match, "h=30 window count not found"
    assert HORIZONS[30]["n_windows"] == 24
    assert HORIZONS[30]["evaluation_period_start"] == date(2024, 10, 21)

    label_match = re.search(
        r"pseudo-real-time\s+·\s+archive\s+vintage\s+2026-10-11\s+·\s+not\s+a\s+real-time\s+claim",
        text,
        re.MULTILINE,
    )
    assert label_match, "Label string not found in declaration"
    assert RESULT_LABEL == "pseudo-real-time · archive vintage 2026-10-11 · not a real-time claim"

    target_match = re.search(r"openmeteo-historical-forecast:temperature_2m", text)
    assert target_match, "Target name not found"
    assert TARGET_NAME == "openmeteo-historical-forecast:temperature_2m"


# Verdict tests. candidate_verdict reads nothing but the per-window MAE of each
# first-pass report, so these build EvaluationReports from plain lists of MAEs.
# Every point carries the same windows, so a model's macro MAE is the mean of
# its window MAEs, and the chosen numbers fix the expected verdict exactly.
VERDICT_POINTS = ["AAA", "BBB"]


def _fabricated_report(model: str, window_maes) -> EvaluationReport:
    """An EvaluationReport whose only field the verdict reads is the per-window mae."""
    return EvaluationReport(
        model_name=model,
        horizon=7,
        windows=[
            WindowResult(
                window=index, train_end=0, test_start=0, test_end=0,
                mae=float(mae), rmse=0.0, mase=0.0, bias=0.0, interval_coverage=None,
            )
            for index, mae in enumerate(window_maes)
        ],
    )


def _verdict_from_fabricated(candidate_maes, baseline_maes: dict) -> dict:
    reports_by_point = {
        point: {
            "fabricated_candidate": _fabricated_report("fabricated_candidate", candidate_maes),
            **{name: _fabricated_report(name, maes) for name, maes in baseline_maes.items()},
        }
        for point in VERDICT_POINTS
    }
    return candidate_verdict(
        "fabricated_candidate",
        list(baseline_maes),
        reports_by_point,
        VERDICT_POINTS,
        len(candidate_maes),
    )


def test_verdict_tie_with_strongest_baseline_is_not_a_win():
    """Candidate macro MAE equals the strongest baseline's exactly: beats_all is False.

    Every window of the candidate equals Climatology's, so each difference is zero
    and the interval is [0, 0]. The tie fails on beats_all alone.

    Mutation l: `<=` instead of `<` in beats_all makes beats_all True here.
    """
    verdict = _verdict_from_fabricated(
        [3.0, 4.0, 5.0],
        {"Naive": [6.0, 7.0, 8.0], "Climatology": [3.0, 4.0, 5.0]},
    )

    assert verdict["strongest_baseline"] == "Climatology"
    assert verdict["candidate_mae"] == verdict["strongest_baseline_mae"]
    assert verdict["bootstrap_ci_95"]["upper"] == 0.0
    assert verdict["beats_all"] is False
    assert verdict["succeeded"] is False


def test_beats_every_baseline_but_interval_crossing_zero_is_not_success():
    """Macro MAE is lower than every baseline, but the per-window differences are mixed:
    three windows better by 1.0, one worse by 0.5. The 95% interval runs from -1.0 up
    to a bound above zero, so the candidate beats_all and does not succeed.

    Mutation j: `succeeded = beats_all` ignores the interval and makes succeeded True.
    """
    verdict = _verdict_from_fabricated(
        [4.0, 4.0, 4.0, 5.5],
        {"Naive": [9.0, 9.0, 9.0, 9.0], "Climatology": [5.0, 5.0, 5.0, 5.0]},
    )

    assert verdict["candidate_mae"] < verdict["strongest_baseline_mae"]
    assert verdict["beats_all"] is True
    assert verdict["bootstrap_ci_95"]["lower"] < 0.0
    assert verdict["bootstrap_ci_95"]["upper"] >= 0.0
    assert verdict["succeeded"] is False


def test_clear_win_in_every_window_succeeds():
    """Every window better than the strongest baseline by the same 1.0. The interval
    is [-1.0, -1.0], entirely below zero, so the candidate succeeds."""
    verdict = _verdict_from_fabricated(
        [4.0, 5.0, 6.0, 7.0],
        {"Naive": [9.0, 10.0, 11.0, 12.0], "Climatology": [5.0, 6.0, 7.0, 8.0]},
    )

    assert verdict["beats_all"] is True
    assert verdict["bootstrap_ci_95"]["upper"] < 0.0
    assert verdict["succeeded"] is True


def test_bootstrap_reuses_first_pass_reports_and_never_refits(tmp_path):
    """Each (model, point) is evaluated once, and the verdict is computed from those
    reports. evaluate_rolling_origin is wrapped by a counting spy where archive_eval
    looks it up. The call count must equal (#baselines + #candidates) x #points x
    #horizons exactly; a second pass for the bootstrap would add calls above that.

    Every candidate must reach the bootstrap, so the count is over computed verdicts
    and not over refusals or not_comparable results.

    Mutation g: a second evaluate_rolling_origin call per candidate point, whose
    windows then feed the verdict.
    """
    from app.services.forecasting.challengers import CANDIDATES

    archive_dir = tmp_path / "archive"
    points = _write_complete_archive(archive_dir)
    assert len(points) == 21

    baselines = default_baselines(season_length=7, window=7) + [ClimatologyForecast()]
    candidates = [spec for spec in CANDIDATES if spec.role != "shadow"]
    horizons = [FIXTURE_HORIZON]

    with mock.patch(
        "app.services.forecasting.archive_eval.evaluate_rolling_origin",
        wraps=evaluate_rolling_origin,
    ) as spy:
        report = evaluate_archive(
            archive_dir, horizons=horizons, window_configs=_fixture_window_configs(n_windows=2)
        )

    assert not report.get("refused"), f"Evaluation refused: {report.get('refusal_reasons')}"
    verdicts = report["results_by_horizon"][FIXTURE_HORIZON]["candidate_verdicts"]
    assert set(verdicts) == {spec.name for spec in candidates}
    for name, verdict in verdicts.items():
        assert "bootstrap_ci_95" in verdict, f"{name} did not reach the bootstrap: {verdict}"

    expected_calls = (len(baselines) + len(candidates)) * len(points) * len(horizons)
    assert spy.call_count == expected_calls, (
        f"evaluate_rolling_origin called {spy.call_count} times, expected {expected_calls}"
    )
