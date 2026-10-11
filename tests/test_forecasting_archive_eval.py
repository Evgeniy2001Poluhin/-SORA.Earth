"""Tests for M3-A archive evaluation (preregistered forecast on archived series).

These tests verify the implementation of docs/M3A_ARCHIVE_DECLARATION.md before
the first real run on the archive. The declaration was committed first, and
these tests ensure the code honours it.

Each behavioral test is proven: the test is run with the code temporarily broken
to confirm it catches the defect, then restored and run green.
"""
from __future__ import annotations

import gzip
import importlib.util
import inspect
import json
import os
import re
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import pytest

from app.services.forecasting import archive_eval
from app.services.forecasting.archive_eval import (
    ARCHIVE_PERIOD_END,
    ARCHIVE_PERIOD_START,
    DECLARATION,
    DECLARED_FAMILY_SIZE,
    DECLARED_HORIZONS,
    EXPECTED_MANIFEST_DIGEST,
    FAMILY_ALPHA,
    FROZEN_CANDIDATES,
    HORIZONS,
    PER_CLAIM_ALPHA,
    RESULT_LABEL,
    TARGET_NAME,
    ProvenanceError,
    block_length,
    candidate_verdict,
    circular_block_resamples,
    collect_provenance,
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


def _complete_payloads() -> dict:
    """Hourly temperature for every declared point, FIXTURE_START..FIXTURE_END, keyed
    by (point, year) and holding (times, temperatures).

    Each point is an annual sine around its own level plus seeded noise, with 24
    hourly values per day and no gaps. Tests take this and remove or corrupt what
    they need, then write it with _write_payloads.
    """
    from app.ingesters.openmeteo import REGION_CAPITALS

    points = sorted(code for code, _lat, _lon in REGION_CAPITALS)

    rng = np.random.default_rng(20181231)
    n_days = (FIXTURE_END - FIXTURE_START).days + 1
    days = [FIXTURE_START + timedelta(days=i) for i in range(n_days)]
    annual = np.sin(2 * np.pi * np.arange(n_days) / 365.25)
    hour_wave = 2.0 * np.sin(2 * np.pi * np.arange(24) / 24)  # sums to zero over a day
    hour_suffixes = [f"T{hour:02d}:00" for hour in range(24)]

    payloads: dict = {}
    for index, point in enumerate(points):
        daily = _point_level(index) + 8.0 * annual + rng.normal(0.0, 0.05, size=n_days)
        by_year: dict[int, tuple[list[str], list[float]]] = {}
        for day, value in zip(days, daily):
            times, temps = by_year.setdefault(day.year, ([], []))
            stamp = day.isoformat()
            times.extend(f"{stamp}{suffix}" for suffix in hour_suffixes)
            temps.extend((value + hour_wave).tolist())
        for year, payload in by_year.items():
            payloads[(point, year)] = payload
    return payloads


def _write_payloads(archive_dir: Path, payloads: dict) -> None:
    """Write (times, temperatures) payloads as weather/<POINT>_<YEAR>.json.gz files."""
    weather_dir = archive_dir / "weather"
    weather_dir.mkdir(parents=True, exist_ok=True)
    for (point, year), (times, temps) in payloads.items():
        file_path = weather_dir / f"{point}_{year}.json.gz"
        with gzip.open(file_path, "wt", encoding="utf-8", compresslevel=1) as f:
            json.dump({"hourly": {"time": times, "temperature_2m": temps}}, f)


def _write_complete_archive(archive_dir: Path) -> list[str]:
    """Write the complete fixture archive. Returns the declared point codes, sorted."""
    from app.ingesters.openmeteo import REGION_CAPITALS

    _write_payloads(archive_dir, _complete_payloads())
    return sorted(code for code, _lat, _lon in REGION_CAPITALS)


def _without(payload, drop) -> tuple[list[str], list[float]]:
    """The payload with every hour whose timestamp satisfies drop() removed."""
    times, temps = payload
    kept = [(t, v) for t, v in zip(times, temps) if not drop(t)]
    return [t for t, _ in kept], [v for _, v in kept]


def _evaluate_fixture(archive_dir: Path, *, n_windows: int = FIXTURE_WINDOWS, **overrides):
    """evaluate_archive over a synthetic archive.

    The digest is the archive's own, and the grid bounds are the fixture's, where a
    real run uses the pinned digest and the declared period. The clean-tree check is
    off because these tests run in a checkout that may be dirty; the provenance tests
    cover that check. Keyword overrides replace any of these.
    """
    kwargs = {
        "horizons": [FIXTURE_HORIZON],
        "window_configs": _fixture_window_configs(n_windows),
        "expected_manifest_digest": manifest_digest(archive_dir),
        "period_start": FIXTURE_START,
        "period_end": FIXTURE_END,
        "require_clean_tree": False,
    }
    kwargs.update(overrides)
    return evaluate_archive(archive_dir, **kwargs)


def _fit_spy():
    """Counts fits. evaluate_rolling_origin is the one place every model is fitted, so a
    refusal that happens before any fit leaves its count at zero."""
    return mock.patch(
        "app.services.forecasting.archive_eval.evaluate_rolling_origin",
        wraps=evaluate_rolling_origin,
    )


def _git_in(path: Path, *args: str) -> str:
    """Run git in a throwaway repository, with an identity and no signing."""
    return subprocess.run(
        [
            "git",
            "-c", "user.name=test",
            "-c", "user.email=test@example.invalid",
            "-c", "commit.gpgsign=false",
            *args,
        ],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _init_declared_repo(path: Path, *, dirty: bool) -> None:
    """A throwaway repository with the declaration committed, and optionally a tracked
    modification. The real working tree is never touched."""
    path.mkdir(parents=True)
    _git_in(path, "init", "-q")
    declaration = path / "docs" / "M3A_ARCHIVE_DECLARATION.md"
    declaration.parent.mkdir()
    declaration.write_text("declaration\n", encoding="utf-8")
    _git_in(path, "add", "docs")
    _git_in(path, "commit", "-q", "-m", "declaration")
    if dirty:
        declaration.write_text("declaration, edited\n", encoding="utf-8")


def _load_cli():
    """The CLI script, loaded as a module, without running it."""
    script = REPO_ROOT / "scripts" / "evaluate_forecast_archive.py"
    spec = importlib.util.spec_from_file_location("evaluate_forecast_archive", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        report = _evaluate_fixture(archive_dir)

    assert not report.get("refused"), f"Evaluation refused: {report.get('refusal_reasons')}"
    horizon_result = report["results_by_horizon"][FIXTURE_HORIZON]

    failures = horizon_result["failures"]
    assert set(failures) == {"Climatology(±3d)"}, f"Only Climatology should fail: {failures}"
    assert failures["Climatology(±3d)"]["count"] == 1
    assert failures["Climatology(±3d)"]["first_few"][0].startswith("ITA:")

    expected_candidates = set(FROZEN_CANDIDATES)
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
        report = _evaluate_fixture(archive_dir)

    assert not report.get("refused"), f"Evaluation refused: {report.get('refusal_reasons')}"
    horizon_result = report["results_by_horizon"][FIXTURE_HORIZON]

    failures = horizon_result["failures"]
    assert set(failures) == {"Climatology(±3d)"}, f"Only Climatology should fail: {failures}"
    assert failures["Climatology(±3d)"]["count"] == 21

    expected_candidates = set(FROZEN_CANDIDATES)
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


def _write_two_point_archive(archive_dir: Path) -> None:
    """DEU and FRA only, for 2025 and 2026, days 1..90 of each year. Nineteen declared
    points are absent and the days are not contiguous, so evaluate_archive must refuse."""
    weather_dir = archive_dir / "weather"
    weather_dir.mkdir(parents=True)

    for index, point in enumerate(["DEU", "FRA"]):
        for year in [2025, 2026]:
            times = []
            temps = []
            for day in range(1, 91):
                for hour in range(24):
                    dt = datetime(year, 1, 1) + timedelta(days=day - 1, hours=hour)
                    times.append(dt.strftime("%Y-%m-%dT%H:%M"))
                    temps.append(5.0 + index + np.sin(day / 7) * 3 + hour / 24)

            hourly_data = {"hourly": {"time": times, "temperature_2m": temps}}
            file_path = weather_dir / f"{point}_{year}.json.gz"
            with gzip.open(file_path, "wt", encoding="utf-8") as f:
                json.dump(hourly_data, f)


def test_end_to_end_report_on_complete_archive(tmp_path):
    """A complete 21-point archive yields an unrefused report. Every candidate carries a
    verdict with its bootstrap interval, the header fields are the module constants, and
    a second run reproduces the first apart from generated_at.

    Two windows, so the bootstrap has something to resample."""
    archive_dir = tmp_path / "archive"
    points = _write_complete_archive(archive_dir)
    assert len(points) == 21

    def run_evaluation():
        return _evaluate_fixture(archive_dir, n_windows=2)

    report = run_evaluation()

    assert report["refused"] is False, f"Evaluation refused: {report.get('refusal_reasons')}"
    assert report["label"] == RESULT_LABEL
    assert report["target"] == TARGET_NAME
    assert report["declaration"] == DECLARATION

    verdicts = report["results_by_horizon"][FIXTURE_HORIZON]["candidate_verdicts"]
    expected_candidates = set(FROZEN_CANDIDATES)
    assert set(verdicts) == expected_candidates, f"Verdicts: {verdicts}"

    for name, verdict in verdicts.items():
        assert {"beats_every_baseline", "succeeded", "per_baseline"} <= set(verdict), (
            f"{name} has no complete verdict: {verdict}"
        )
        assert set(verdict["per_baseline"]) == set(ALL_BASELINES), f"{name}: {verdict}"
        for baseline_name, entry in verdict["per_baseline"].items():
            assert {"mean_difference", "upper_bound", "passes"} <= set(entry), (
                f"{name} against {baseline_name}: {entry}"
            )

    second = run_evaluation()
    first_comparable = dict(report)
    first_comparable.pop("generated_at")
    second_comparable = dict(second)
    second_comparable.pop("generated_at")
    assert first_comparable == second_comparable


def test_end_to_end_refuses_an_incomplete_point_set(tmp_path):
    """A two-point archive is refused. The refusal names exactly the declared points
    that are absent, and no results are computed."""
    from app.ingesters.openmeteo import REGION_CAPITALS

    archive_dir = tmp_path / "archive"
    _write_two_point_archive(archive_dir)

    report = evaluate_archive(
        archive_dir,
        horizons=[7],
        expected_manifest_digest=manifest_digest(archive_dir),
        require_clean_tree=False,
    )

    declared = sorted(code for code, _lat, _lon in REGION_CAPITALS)
    expected_missing = sorted(set(declared) - {"DEU", "FRA"})
    assert report["refused"] is True
    assert report["refusal_reasons"]["missing_declared_points"] == expected_missing
    assert "results_by_horizon" not in report


def test_constants_match_declaration_document():
    """Constants in the module must match the declaration text exactly."""
    declaration_path = REPO_ROOT / "docs" / "M3A_ARCHIVE_DECLARATION.md"
    assert declaration_path.exists(), (
        f"Declaration file is part of the repository and is missing: {declaration_path}"
    )

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

    # The declaration this code claims to implement, and the vintage it pins.
    assert DECLARATION == "docs/M3A_ARCHIVE_DECLARATION.md v1.1"
    latest_version = re.findall(r"^\| (\d+\.\d+) \|", text, re.MULTILINE)[-1]
    assert DECLARATION.endswith(f"v{latest_version}"), (
        f"DECLARATION {DECLARATION!r} is not the latest amendment {latest_version}"
    )
    assert EXPECTED_MANIFEST_DIGEST in text, "pinned manifest digest is not in the declaration"

    section_8 = text.split("## 8. Candidates")[1].split("## 9.")[0]
    for name in FROZEN_CANDIDATES:
        assert f"`{name}`" in section_8, f"frozen candidate {name} is not listed in section 8"


# Verdict tests (M3-A §9). candidate_verdict reads only the window MAEs and the
# window positions of each first-pass report, so these build EvaluationReports by
# hand on a synthetic calendar. Every point carries the same windows unless a test
# says otherwise, so a model's macro MAE is the mean of its window MAEs.
VERDICT_POINTS = ["AAA", "BBB"]
VERDICT_HORIZON = 7
VERDICT_CALENDAR = [date(2018, 1, 1) + timedelta(days=i) for i in range(4000)]
NAIVE, SEASONAL, MOVING_AVERAGE, CLIMATOLOGY = [
    baseline.model_name
    for baseline in [*default_baselines(season_length=7, window=7), ClimatologyForecast()]
]
ALL_BASELINES = [NAIVE, SEASONAL, MOVING_AVERAGE, CLIMATOLOGY]
CANDIDATE_MAE = 10.0

# Per-window differences (candidate minus baseline) over 24 windows, the h=30 size
# (L = 3, 8 blocks). Each was found against circular_block_resamples(24) so that a
# premise holds; every test that uses one asserts that premise itself.
#   mean -0.42, 95th-percentile bound < 0, 98.75th-percentile bound >= 0
DIFFS_BONFERRONI = np.array([0.83, -1.67, 1.83, -2.67] * 6)
#   mean -0.20, 98.75th-percentile bound >= 0
DIFFS_MOVING_AVERAGE = np.array([1.05, -1.45, 2.05, -2.45] * 6)


def _report(model: str, window_maes, *, horizon: int = VERDICT_HORIZON, shift=None) -> EvaluationReport:
    """A report with the given window MAEs. Window k covers the same days in every
    report, unless shift=(k, days) moves that one window's positions by `days`."""
    windows = []
    for index, mae in enumerate(window_maes):
        train_end = 1000 + horizon * index
        test_start, test_end = train_end, train_end + horizon
        if shift is not None and shift[0] == index:
            train_end, test_start, test_end = (p + shift[1] for p in (train_end, test_start, test_end))
        windows.append(WindowResult(
            window=index, train_end=train_end, test_start=test_start, test_end=test_end,
            mae=float(mae), rmse=0.0, mase=0.0, bias=0.0, interval_coverage=None,
        ))
    return EvaluationReport(model_name=model, horizon=horizon, windows=windows)


def _reports_by_point(candidate_by_point, baseline_maes) -> dict:
    """reports_by_point for VERDICT_POINTS. Each point's candidate report is given;
    each baseline's report (name -> window MAEs) is the same on every point."""
    return {
        point: {
            "fabricated_candidate": candidate_by_point[point],
            **{name: _report(name, maes) for name, maes in baseline_maes.items()},
        }
        for point in VERDICT_POINTS
    }


def _verdict(reports_by_point, baselines, n_windows, *, resample_indices=None,
             per_claim_alpha=PER_CLAIM_ALPHA) -> dict:
    if resample_indices is None:
        resample_indices = circular_block_resamples(n_windows)
    return candidate_verdict(
        "fabricated_candidate", list(baselines), reports_by_point, VERDICT_POINTS,
        n_windows, resample_indices, VERDICT_CALENDAR, per_claim_alpha=per_claim_alpha,
    )


def _verdict_from_fabricated(candidate_maes, baseline_maes, **kwargs) -> dict:
    candidate = {point: _report("fabricated_candidate", candidate_maes) for point in VERDICT_POINTS}
    return _verdict(
        _reports_by_point(candidate, baseline_maes), list(baseline_maes), len(candidate_maes), **kwargs
    )


def _verdict_for_diffs(diffs_by_baseline: dict, **kwargs) -> dict:
    """A verdict where the candidate's window MAE is CANDIDATE_MAE and each baseline's
    window MAE is CANDIDATE_MAE minus the given per-window difference."""
    n_windows = len(next(iter(diffs_by_baseline.values())))
    candidate = {
        point: _report("fabricated_candidate", np.full(n_windows, CANDIDATE_MAE))
        for point in VERDICT_POINTS
    }
    baseline_maes = {
        name: CANDIDATE_MAE - np.asarray(diffs, dtype=float)
        for name, diffs in diffs_by_baseline.items()
    }
    return _verdict(
        _reports_by_point(candidate, baseline_maes), list(diffs_by_baseline), n_windows, **kwargs
    )


def _bootstrap_upper(diffs, per_claim_alpha: float = PER_CLAIM_ALPHA) -> float:
    """The one-sided bound of the block bootstrap of mean(diffs), for a premise check."""
    diffs = np.asarray(diffs, dtype=float)
    resamples = circular_block_resamples(len(diffs))
    return float(np.percentile(diffs[resamples].mean(axis=1), 100.0 * (1.0 - per_claim_alpha)))


def test_block_length_is_ceil_cube_root_for_both_declared_sizes():
    """M3-A §9: L = ceil(n ** (1/3)). h=7 has 104 windows, so L = 5; h=30 has 24, so L = 3.
    64 is an exact cube whose floating-point cube root is 3.9999999999999996, so the
    integer rule must give 4 where truncation gives 3.

    Mutation a: int(n ** (1/3)) instead of ceil gives 4 for 104, red on the first assert."""
    assert block_length(104) == 5
    assert block_length(24) == 3
    assert block_length(27) == 3
    assert block_length(28) == 4
    assert block_length(64) == 4


@pytest.mark.parametrize("n_windows", [104, 24])
def test_resamples_are_circular_blocks_of_the_declared_length(n_windows):
    """Each resample is ceil(n / L) block starts, each expanded to L consecutive window
    indices that wrap modulo n, concatenated and truncated to n. The sequences are
    rebuilt here from their block starts and compared whole. A block that wraps
    past the last window must occur, so the wrap is exercised and not only allowed.

    Mutation: no modulo (a block cannot wrap) makes the rebuilt sequences differ."""
    length = block_length(n_windows)
    resamples = circular_block_resamples(n_windows)
    assert resamples.shape == (10_000, n_windows)
    starts = resamples[:, ::length]
    assert starts.shape[1] == -(-n_windows // length)

    rebuilt = ((starts[:, :, None] + np.arange(length)) % n_windows).reshape(len(resamples), -1)
    assert np.array_equal(rebuilt[:, :n_windows], resamples)

    within = np.array([i for i in range(n_windows - 1) if (i + 1) % length != 0])
    assert np.all((resamples[:, within + 1] - resamples[:, within]) % n_windows == 1)
    wraps = (resamples[:, within] == n_windows - 1) & (resamples[:, within + 1] == 0)
    assert wraps.any(), "no resample contains a block that wraps past the last window"


def test_one_resample_set_per_horizon_is_shared_by_every_claim(tmp_path):
    """The resampled index sequences are generated once per horizon and the same array
    is handed to every candidate's verdict. The fixture has one horizon and two
    candidates, so the generator runs once and both verdicts receive the same object.

    Mutation: generating the sequences inside the claim loop gives two generator calls
    and two different objects, red on both asserts."""
    archive_dir = tmp_path / "archive"
    _write_complete_archive(archive_dir)

    with mock.patch.object(
        archive_eval, "circular_block_resamples", wraps=circular_block_resamples
    ) as generator, mock.patch.object(
        archive_eval, "candidate_verdict", wraps=candidate_verdict
    ) as verdict_spy:
        report = _evaluate_fixture(archive_dir, n_windows=2)

    assert not report.get("refused"), f"Evaluation refused: {report.get('refusal_reasons')}"
    assert generator.call_count == 1
    shared = [call.args[5] for call in verdict_spy.call_args_list]
    assert len(shared) == len(FROZEN_CANDIDATES)
    assert all(array is shared[0] for array in shared)


def test_claim_beating_three_baselines_but_not_the_fourth_is_not_success():
    """Candidate MAE 10.0. Naive, SeasonalNaive and Climatology are beaten on every
    window. MovingAverage is beaten by 0.20 in the mean, but the candidate's advantage
    is uneven across windows, so its 98.75th-percentile bound is above zero.

    Climatology is the strongest baseline by macro MAE, and the candidate beats it on
    every window. A rule that tests only the strongest baseline therefore succeeds.
    Every-baseline does not.

    A tie or loss against the strongest baseline cannot make this fixture, because the
    strongest baseline has the lowest MAE of all, so every rule would fail it.

    Mutation: compare only the strongest baseline -> succeeded becomes True, red."""
    diffs = {
        NAIVE: np.full(24, -4.0),
        SEASONAL: np.full(24, -3.0),
        MOVING_AVERAGE: DIFFS_MOVING_AVERAGE,
        CLIMATOLOGY: np.full(24, -0.1),
    }
    assert all(values.mean() < 0 for values in diffs.values()), "candidate must beat every baseline in the mean"
    assert _bootstrap_upper(diffs[CLIMATOLOGY]) < 0, "premise: the strongest baseline passes"
    assert _bootstrap_upper(diffs[MOVING_AVERAGE]) >= 0, "premise: MovingAverage fails"
    assert max(diffs, key=lambda name: diffs[name].mean()) == CLIMATOLOGY

    verdict = _verdict_for_diffs(diffs)

    assert verdict["beats_every_baseline"] is True
    assert verdict["per_baseline"][CLIMATOLOGY]["passes"] is True
    assert verdict["per_baseline"][MOVING_AVERAGE]["passes"] is False
    assert verdict["succeeded"] is False


def test_claim_is_tested_at_98_75_percent_not_95():
    """Bonferroni over the family of 4 claims: each claim is one-sided at 0.0125, so the
    bound is the 98.75th percentile. The fixture's 95th-percentile bound is below zero
    and its 98.75th is not, so a claim tested at 95 would succeed and this one must not.

    Mutation: PER_CLAIM_ALPHA = FAMILY_ALPHA (0.05) makes the bound the 95th percentile,
    which passes; the constant assertion and succeeded both go red."""
    assert FAMILY_ALPHA == 0.05
    assert DECLARED_HORIZONS == (7, 30)
    assert DECLARED_FAMILY_SIZE == len(FROZEN_CANDIDATES) * len(DECLARED_HORIZONS) == 4
    assert PER_CLAIM_ALPHA == 0.0125

    diffs = DIFFS_BONFERRONI
    assert diffs.mean() < 0
    upper_95 = _bootstrap_upper(diffs, per_claim_alpha=0.05)
    upper_98_75 = _bootstrap_upper(diffs, per_claim_alpha=0.0125)
    assert upper_95 < 0 <= upper_98_75, f"premise: 95th {upper_95}, 98.75th {upper_98_75}"

    verdict = _verdict_for_diffs({NAIVE: diffs})

    assert verdict["per_claim_alpha"] == 0.0125
    assert verdict["per_baseline"][NAIVE]["upper_bound"] == pytest.approx(upper_98_75)
    assert verdict["per_baseline"][NAIVE]["passes"] is False
    assert verdict["succeeded"] is False


def test_windows_are_paired_by_date_not_by_count():
    """Four windows everywhere, but the candidate's window 2 on BBB sits one day later
    than on AAA. The claim is not_comparable, and the reason names the candidate, the
    point and the window. Control: the unshifted reports pair and get a verdict.

    Mutation: pairing checked by counts only -> a verdict is computed, red."""
    maes = [4.0, 5.0, 6.0, 7.0]
    baselines = {NAIVE: [9.0, 10.0, 11.0, 12.0], CLIMATOLOGY: [5.0, 6.0, 7.0, 8.0]}

    control = _verdict(
        _reports_by_point({p: _report("fabricated_candidate", maes) for p in VERDICT_POINTS}, baselines),
        [NAIVE, CLIMATOLOGY], 4,
    )
    assert control.get("reason") != "not_comparable", f"control refused: {control}"

    shifted = {
        "AAA": _report("fabricated_candidate", maes),
        "BBB": _report("fabricated_candidate", maes, shift=(2, 1)),
    }
    verdict = _verdict(_reports_by_point(shifted, baselines), [NAIVE, CLIMATOLOGY], 4)

    assert verdict["succeeded"] is False
    assert verdict.get("reason") == "not_comparable", verdict
    assert "fabricated_candidate/BBB window 2" in verdict.get("pairing", ""), verdict
    assert "fabricated_candidate/AAA window 2" in verdict["pairing"]


def test_non_finite_window_mae_is_not_comparable():
    """One window MAE of the candidate on AAA is NaN. The claim is not_comparable,
    and the reason names that model, point and window, and says it is non-finite.
    Control: the same reports with a finite value get a verdict.

    Mutation: no finiteness check -> a verdict is computed with NaN, red."""
    baselines = {NAIVE: [9.0, 10.0, 11.0, 12.0], CLIMATOLOGY: [5.0, 6.0, 7.0, 8.0]}

    control = _verdict(
        _reports_by_point({p: _report("fabricated_candidate", [4.0, 5.0, 6.0, 7.0]) for p in VERDICT_POINTS}, baselines),
        [NAIVE, CLIMATOLOGY], 4,
    )
    assert control.get("reason") != "not_comparable", f"control refused: {control}"

    poisoned = {
        "AAA": _report("fabricated_candidate", [4.0, float("nan"), 6.0, 7.0]),
        "BBB": _report("fabricated_candidate", [4.0, 5.0, 6.0, 7.0]),
    }
    verdict = _verdict(_reports_by_point(poisoned, baselines), [NAIVE, CLIMATOLOGY], 4)

    assert verdict["succeeded"] is False
    assert verdict.get("reason") == "not_comparable", verdict
    assert "fabricated_candidate/AAA window 1" in verdict.get("pairing", ""), verdict
    assert "non-finite" in verdict.get("pairing", ""), verdict


def test_clear_win_over_every_baseline_succeeds():
    """The candidate beats each of the four baselines in every window, by 4, 3, 2 and 1.
    Every bound is below zero, so the claim succeeds."""
    diffs = {
        NAIVE: np.full(24, -4.0),
        SEASONAL: np.full(24, -3.0),
        MOVING_AVERAGE: np.full(24, -2.0),
        CLIMATOLOGY: np.full(24, -1.0),
    }
    verdict = _verdict_for_diffs(diffs)

    assert verdict["beats_every_baseline"] is True
    assert verdict["succeeded"] is True
    for name in ALL_BASELINES:
        entry = verdict["per_baseline"][name]
        assert entry["passes"] is True, f"{name}: {entry}"
        assert entry["upper_bound"] < 0.0, f"{name}: {entry}"
        assert entry["mean_difference"] == pytest.approx(diffs[name].mean())


def test_power_note_below_thirty_windows_and_absent_at_104():
    """M3-A §9, power: a claim with fewer than 30 windows carries the note. At 24 windows
    that is 8 blocks. At 104 windows there is no note.

    Mutation: power_note always attached -> the 104-window verdict carries it, red."""
    short = _verdict_for_diffs({NAIVE: np.full(24, -1.0)})
    assert short["power_note"] == (
        "24 windows in 8 blocks: a failure here is weak evidence that no effect exists"
    )

    long_run = _verdict_for_diffs({NAIVE: np.full(104, -1.0)})
    assert "power_note" not in long_run
    assert long_run["n_blocks"] == 21
    assert long_run["block_length"] == 5


def test_tie_with_strongest_baseline_is_not_a_win():
    """Candidate and Climatology have identical windows, so every difference is zero.
    The mean difference is zero, the bound is 0.0, and the bound is not below zero.
    Ties lose on beats_every_baseline and on the strict bound."""
    verdict = _verdict_from_fabricated(
        [3.0, 4.0, 5.0],
        {NAIVE: [6.0, 7.0, 8.0], CLIMATOLOGY: [3.0, 4.0, 5.0]},
    )

    tie = verdict["per_baseline"][CLIMATOLOGY]
    assert tie["mean_difference"] == 0.0
    assert tie["upper_bound"] == 0.0
    assert tie["passes"] is False
    assert verdict["beats_every_baseline"] is False
    assert verdict["succeeded"] is False


def test_beats_every_baseline_but_bound_crossing_zero_is_not_success():
    """The candidate is lower than both baselines in macro MAE, but MovingAverage's
    per-window advantage is uneven, so its bound is above zero. The claim does not
    succeed, though one baseline passes.

    Mutation j: succeeded = beats_every_baseline ignores the bounds, red."""
    diffs = {NAIVE: np.full(24, -4.0), MOVING_AVERAGE: DIFFS_MOVING_AVERAGE}
    assert _bootstrap_upper(DIFFS_MOVING_AVERAGE) >= 0, "premise"

    verdict = _verdict_for_diffs(diffs)

    assert verdict["beats_every_baseline"] is True
    assert verdict["per_baseline"][NAIVE]["passes"] is True
    assert verdict["per_baseline"][MOVING_AVERAGE]["passes"] is False
    assert verdict["succeeded"] is False


def test_family_is_declared_horizons_and_bonferroni_alpha(tmp_path):
    """The family is FROZEN_CANDIDATES x the declared horizons, 2 x 2 = 4. The report
    stores both that number and the number of claims in the run, so a partial run
    cannot be read as the family. The default run is official; a fixture run with
    horizon 2 is not, and still uses the declared family size for its alpha.

    The default run is refused here, on purpose: the incomplete archive stops
    evaluation before any horizon runs, and the family record still appears."""
    archive_dir = tmp_path / "archive"
    _write_two_point_archive(archive_dir)

    refused = evaluate_archive(
        archive_dir,
        expected_manifest_digest=manifest_digest(archive_dir),
        require_clean_tree=False,
    )
    assert refused["refused"] is True
    assert refused["family"] == {
        "alpha": 0.05,
        "claims_declared": 4,
        "claims_in_this_run": 4,
        "per_claim_alpha": 0.0125,
        "horizons_declared": [7, 30],
        "horizons_run": [7, 30],
        "official": True,
    }

    partial_dir = tmp_path / "complete"
    _write_complete_archive(partial_dir)
    partial = _evaluate_fixture(partial_dir, n_windows=2)
    assert partial["family"]["claims_declared"] == 4
    assert partial["family"]["claims_in_this_run"] == 2
    assert partial["family"]["per_claim_alpha"] == 0.0125
    assert partial["family"]["official"] is False



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
    archive_dir = tmp_path / "archive"
    points = _write_complete_archive(archive_dir)
    assert len(points) == 21

    baselines = default_baselines(season_length=7, window=7) + [ClimatologyForecast()]
    horizons = [FIXTURE_HORIZON]

    with _fit_spy() as spy:
        report = _evaluate_fixture(archive_dir, n_windows=2)

    assert not report.get("refused"), f"Evaluation refused: {report.get('refusal_reasons')}"
    verdicts = report["results_by_horizon"][FIXTURE_HORIZON]["candidate_verdicts"]
    assert set(verdicts) == set(FROZEN_CANDIDATES)
    for name, verdict in verdicts.items():
        assert "per_baseline" in verdict, f"{name} did not reach the bootstrap: {verdict}"

    expected_calls = (len(baselines) + len(FROZEN_CANDIDATES)) * len(points) * len(horizons)
    assert spy.call_count == expected_calls, (
        f"evaluate_rolling_origin called {spy.call_count} times, expected {expected_calls}"
    )


# --- Input integrity (M3-A §3) ----------------------------------------------
#
# Each refusal below must happen before any model is fitted. The tests that expect
# one assert that the fit spy was called zero times.


def test_missing_first_year_is_refused_before_any_fit(tmp_path):
    """The case the old check passed. The days that are present are contiguous, so a
    series with all of 2018 absent looked complete. Against the grid, 2018 is zero
    hours at every point, and the first observed day is 2019-01-01.

    Mutation: check only the observed days, as the old code did, and drop the first
    and last day comparisons. The run is then not refused.
    """
    archive_dir = tmp_path / "archive"
    _write_complete_archive(archive_dir)
    for file_path in list((archive_dir / "weather").glob("*_2018.json.gz")):
        file_path.unlink()

    with _fit_spy() as spy:
        report = _evaluate_fixture(archive_dir)

    assert report["refused"] is True
    reasons = report["refusal_reasons"]
    assert reasons["first_day"] == {"expected": "2018-01-01", "observed": "2019-01-01"}
    assert reasons["point_days_below_floor"] == 21 * 365
    assert "results_by_horizon" not in report
    assert spy.call_count == 0, f"{spy.call_count} fits ran on a refused archive"


def test_one_point_missing_one_day_is_refused(tmp_path):
    """DEU has no hours on 2019-03-05. The other twenty points do. The grid names
    exactly that point-day, at zero hours.

    Mutation: remove the per-point-day floor check from _integrity_refusals.
    """
    payloads = _complete_payloads()
    payloads[("DEU", 2019)] = _without(
        payloads[("DEU", 2019)], lambda t: t.startswith("2019-03-05T")
    )
    archive_dir = tmp_path / "archive"
    _write_payloads(archive_dir, payloads)

    with _fit_spy() as spy:
        report = _evaluate_fixture(archive_dir)

    assert report["refused"] is True
    reasons = report["refusal_reasons"]
    assert reasons["point_days_below_floor"] == 1
    assert reasons["first_few_below_floor"] == ["DEU 2019-03-05: 0 h"]
    assert spy.call_count == 0


def test_one_day_missing_at_every_point_is_refused(tmp_path):
    """2019-03-05 is absent everywhere: twenty-one point-days at zero hours. The first
    and last days are present, so only the grid count can see it.

    Mutation: remove the per-point-day floor check from _integrity_refusals.
    """
    payloads = {
        key: _without(payload, lambda t: t.startswith("2019-03-05T"))
        for key, payload in _complete_payloads().items()
    }
    archive_dir = tmp_path / "archive"
    _write_payloads(archive_dir, payloads)

    with _fit_spy() as spy:
        report = _evaluate_fixture(archive_dir)

    assert report["refused"] is True
    reasons = report["refusal_reasons"]
    assert reasons["point_days_below_floor"] == 21
    assert "first_day" not in reasons and "last_day" not in reasons
    assert spy.call_count == 0


def test_last_day_missing_is_refused(tmp_path):
    """2020-12-31 is absent at every point. The last observed day is 2020-12-30, and
    the grid's last day is the declared end.

    Mutation: remove the last-day comparison from _integrity_refusals.
    """
    payloads = {
        key: _without(payload, lambda t: t.startswith("2020-12-31T"))
        for key, payload in _complete_payloads().items()
    }
    archive_dir = tmp_path / "archive"
    _write_payloads(archive_dir, payloads)

    with _fit_spy() as spy:
        report = _evaluate_fixture(archive_dir)

    assert report["refused"] is True
    reasons = report["refusal_reasons"]
    assert "last_day" in reasons, f"no last-day reason: {sorted(reasons)}"
    assert reasons["last_day"] == {"expected": "2020-12-31", "observed": "2020-12-30"}
    assert reasons["point_days_below_floor"] == 21
    assert spy.call_count == 0


def test_point_day_with_18_hours_is_refused_and_19_accepted(tmp_path):
    """A point-day with 18 distinct hours is below the 19-hour floor and is refused.
    The same day with 19 hours is accepted, and the run then fits.

    Mutation: lower the floor by one (`< floor` to `< floor - 1`). The 18-hour day
    then passes, and the first assertion fails.
    """
    short = _complete_payloads()
    short[("DEU", 2019)] = _without(
        short[("DEU", 2019)],
        lambda t: t.startswith("2019-03-05T") and int(t[11:13]) >= 18,
    )
    short_dir = tmp_path / "short"
    _write_payloads(short_dir, short)

    with _fit_spy() as spy:
        refused = _evaluate_fixture(short_dir)

    assert refused["refused"] is True
    assert refused["refusal_reasons"]["point_days_below_floor"] == 1
    assert refused["refusal_reasons"]["first_few_below_floor"] == ["DEU 2019-03-05: 18 h"]
    assert spy.call_count == 0

    enough = _complete_payloads()
    enough[("DEU", 2019)] = _without(
        enough[("DEU", 2019)],
        lambda t: t.startswith("2019-03-05T") and int(t[11:13]) >= 19,
    )
    enough_dir = tmp_path / "enough"
    _write_payloads(enough_dir, enough)

    with _fit_spy() as spy_ok:
        accepted = _evaluate_fixture(enough_dir)

    assert accepted["refused"] is False, accepted.get("refusal_reasons")
    assert spy_ok.call_count > 0


def test_undeclared_point_is_refused(tmp_path):
    """A file for a point that is not in REGION_CAPITALS. The refusal names it.

    Mutation: report no undeclared points.
    """
    payloads = _complete_payloads()
    payloads[("XXX", 2019)] = payloads[("DEU", 2019)]
    archive_dir = tmp_path / "archive"
    _write_payloads(archive_dir, payloads)

    with _fit_spy() as spy:
        report = _evaluate_fixture(archive_dir)

    assert report["refused"] is True
    assert report["refusal_reasons"]["undeclared_points"] == ["XXX"]
    assert spy.call_count == 0


def test_duplicated_point_hour_is_refused(tmp_path):
    """DEU's first hour appears twice. A duplicated (point, hour) is a corrupt input,
    not something to average away, so the run is refused.

    Mutation: report no duplicated (point, hour).
    """
    payloads = _complete_payloads()
    times, temps = payloads[("DEU", 2019)]
    payloads[("DEU", 2019)] = (times + [times[0]], temps + [temps[0]])
    archive_dir = tmp_path / "archive"
    _write_payloads(archive_dir, payloads)

    with _fit_spy() as spy:
        report = _evaluate_fixture(archive_dir)

    assert report["refused"] is True
    reasons = report["refusal_reasons"]
    assert set(reasons) == {"duplicate_point_hours"}, sorted(reasons)
    assert reasons["duplicate_point_hours"]["count"] == 1
    assert reasons["duplicate_point_hours"]["first_few"][0].startswith(
        "DEU 2019-01-01T00:00:00"
    )
    assert spy.call_count == 0


def test_non_finite_value_is_refused(tmp_path):
    """One temperature is NaN. JSON carries it, and it is refused rather than dropped
    or filled. The day still has 23 hours, so the NaN is the only reason.

    Mutation: accept non-finite values as ordinary readings.
    """
    payloads = _complete_payloads()
    times, temps = payloads[("DEU", 2019)]
    temps[times.index("2019-03-05T12:00")] = float("nan")
    archive_dir = tmp_path / "archive"
    _write_payloads(archive_dir, payloads)

    with _fit_spy() as spy:
        report = _evaluate_fixture(archive_dir)

    assert report["refused"] is True
    assert set(report["refusal_reasons"]) == {"non_finite_values"}, sorted(report["refusal_reasons"])
    assert report["refusal_reasons"]["non_finite_values"]["count"] == 1
    assert spy.call_count == 0


def test_half_past_timestamp_is_refused(tmp_path):
    """One timestamp reads 07:30 where the hourly grid has 07:00. The hour is still
    present, so the off-the-hour timestamp is the only reason.

    Mutation: accept timestamps that are not on the hour.
    """
    payloads = _complete_payloads()
    times, temps = payloads[("DEU", 2019)]
    times[times.index("2019-03-05T07:00")] = "2019-03-05T07:30"
    archive_dir = tmp_path / "archive"
    _write_payloads(archive_dir, payloads)

    with _fit_spy() as spy:
        report = _evaluate_fixture(archive_dir)

    assert report["refused"] is True
    assert set(report["refusal_reasons"]) == {"off_hour_timestamps"}, sorted(report["refusal_reasons"])
    assert report["refusal_reasons"]["off_hour_timestamps"] == {
        "count": 1,
        "first_few": ["DEU 2019-03-05T07:30"],
    }
    assert spy.call_count == 0


def test_digest_mismatch_is_refused_before_any_fit(tmp_path):
    """One byte of one file is altered, and the run is refused on the digest before any
    fit. The byte is the gzip header's modification time, which decompression ignores,
    so the file still parses and the grid still passes. The digest is the only reason.

    Mutation: drop the manifest digest comparison.
    """
    archive_dir = tmp_path / "archive"
    _write_complete_archive(archive_dir)
    pinned = manifest_digest(archive_dir)

    target = archive_dir / "weather" / "DEU_2019.json.gz"
    raw = bytearray(target.read_bytes())
    raw[4] ^= 0x01  # MTIME, the first byte after the two magic bytes, method and flags
    target.write_bytes(bytes(raw))
    altered = manifest_digest(archive_dir)
    assert altered != pinned

    with _fit_spy() as spy:
        report = _evaluate_fixture(archive_dir, expected_manifest_digest=pinned)

    assert report["refused"] is True
    assert set(report["refusal_reasons"]) == {"manifest_digest"}, sorted(report["refusal_reasons"])
    assert report["refusal_reasons"]["manifest_digest"] == {"expected": pinned, "actual": altered}
    assert spy.call_count == 0


def test_synthetic_archive_is_refused_under_the_pinned_digest(tmp_path):
    """Without an override, the run checks the pinned vintage. A synthetic archive is
    refused on the digest and the expected value is EXPECTED_MANIFEST_DIGEST. The
    defaults are part of the contract: the CLI relies on them.

    Mutation: give expected_manifest_digest an empty default.
    """
    archive_dir = tmp_path / "archive"
    _write_complete_archive(archive_dir)

    with _fit_spy() as spy:
        report = evaluate_archive(
            archive_dir,
            horizons=[FIXTURE_HORIZON],
            window_configs=_fixture_window_configs(),
            period_start=FIXTURE_START,
            period_end=FIXTURE_END,
            require_clean_tree=False,
        )

    assert report["refused"] is True
    assert report["refusal_reasons"]["manifest_digest"]["expected"] == EXPECTED_MANIFEST_DIGEST
    assert spy.call_count == 0

    parameters = inspect.signature(evaluate_archive).parameters
    assert parameters["expected_manifest_digest"].default == EXPECTED_MANIFEST_DIGEST
    assert parameters["require_clean_tree"].default is True


def test_missing_frozen_candidate_is_refused_before_any_fit(tmp_path, monkeypatch):
    """If a frozen name is absent from challengers.CANDIDATES, the run refuses and names
    it. The frozen list is never shortened to whatever happens to exist.

    Mutation: drop the missing-candidate refusal.
    """
    from app.services.forecasting import challengers

    monkeypatch.setattr(
        challengers,
        "CANDIDATES",
        [spec for spec in challengers.CANDIDATES if spec.name != "xgboost_lag"],
    )
    archive_dir = tmp_path / "archive"
    _write_complete_archive(archive_dir)

    with _fit_spy() as spy:
        report = _evaluate_fixture(archive_dir)

    assert report["refused"] is True
    assert report["refusal_reasons"]["missing_frozen_candidates"] == ["xgboost_lag"]
    assert spy.call_count == 0


def test_frozen_candidates_are_what_gets_evaluated(tmp_path, monkeypatch):
    """An entry added to challengers.CANDIDATES is not evaluated. The frozen list names
    the candidates, and they are resolved by name.

    Mutation: evaluate every non-shadow entry of challengers.CANDIDATES, as the old code did.
    """
    from app.services.forecasting import challengers

    sentinel_calls: list[int] = []

    def sentinel_factory():
        sentinel_calls.append(1)
        return challengers.CANDIDATES[0].factory()

    extra = challengers.CandidateSpec(
        name="sentinel_extra", factory=sentinel_factory, role="candidate"
    )
    monkeypatch.setattr(challengers, "CANDIDATES", [*challengers.CANDIDATES, extra])

    archive_dir = tmp_path / "archive"
    _write_complete_archive(archive_dir)
    report = _evaluate_fixture(archive_dir)

    assert report["refused"] is False, report.get("refusal_reasons")
    verdicts = report["results_by_horizon"][FIXTURE_HORIZON]["candidate_verdicts"]
    assert set(verdicts) == set(FROZEN_CANDIDATES)
    assert sentinel_calls == [], "the monkeypatched candidate was fitted"


# --- Provenance (M3-A §3) ---------------------------------------------------


def test_successful_report_carries_provenance(tmp_path):
    """A run that goes ahead names the declaration commit, the code commit, the library
    versions, and the digests it was computed from. The commits are full SHA-1 hashes.

    Mutation: write an empty code commit into the report.
    """
    archive_dir = tmp_path / "archive"
    _write_complete_archive(archive_dir)

    report = _evaluate_fixture(archive_dir)

    assert report["refused"] is False, report.get("refusal_reasons")
    assert report["declaration"] == DECLARATION
    for key in ("declaration_sha", "code_sha"):
        assert re.fullmatch(r"[0-9a-f]{40}", report[key]), f"{key} is {report[key]!r}"
    assert set(report["versions"]) == {"python", "numpy", "pandas", "scikit-learn", "xgboost"}
    for name, version in report["versions"].items():
        assert version, f"{name} version is {version!r}"
    assert report["manifest_digest"] == report["expected_manifest_digest"] == manifest_digest(archive_dir)
    assert report["snapshot_digest"].startswith("sha256:")
    assert report["candidates"] == list(FROZEN_CANDIDATES)


def test_official_provenance_refuses_a_dirty_or_shallow_checkout(tmp_path, monkeypatch):
    """collect_provenance names the declaration commit and HEAD for a clean, full
    repository. A tracked modification refuses it. So does a shallow clone of the same
    history, where the declaration commit cannot be determined. Tests that pass
    require_clean_tree=False are exempt from both.

    Mutation: remove the shallow-clone refusal. The shallow case then returns provenance.
    """
    full = tmp_path / "full"
    _init_declared_repo(full, dirty=False)
    monkeypatch.setattr(archive_eval, "REPO_ROOT", full)
    provenance = collect_provenance(require_clean_tree=True)
    assert re.fullmatch(r"[0-9a-f]{40}", provenance["declaration_sha"])
    assert provenance["declaration_sha"] == provenance["code_sha"]

    dirty = tmp_path / "dirty"
    _init_declared_repo(dirty, dirty=True)
    monkeypatch.setattr(archive_eval, "REPO_ROOT", dirty)
    with pytest.raises(ProvenanceError, match="tracked modifications"):
        collect_provenance(require_clean_tree=True)
    assert collect_provenance(require_clean_tree=False)["code_sha"]

    history = tmp_path / "history"
    _init_declared_repo(history, dirty=False)
    (history / "README.md").write_text("second commit\n", encoding="utf-8")
    _git_in(history, "add", "README.md")
    _git_in(history, "commit", "-q", "-m", "second")
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{history}", str(shallow)],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(archive_eval, "REPO_ROOT", shallow)
    with pytest.raises(ProvenanceError, match="shallow clone"):
        collect_provenance(require_clean_tree=True)
    assert collect_provenance(require_clean_tree=False)["code_sha"]


def test_cli_refuses_a_dirty_tree_with_exit_1_and_reads_nothing(tmp_path, monkeypatch, capsys):
    """A dirty tree is refused with exit 1 and a message, before the archive is read.
    The archive directory given here does not exist; reading it would fail differently.

    Mutation: disable the tracked-modification check.
    """
    repo = tmp_path / "repo"
    _init_declared_repo(repo, dirty=True)
    monkeypatch.setattr(archive_eval, "REPO_ROOT", repo)

    code = _load_cli().main(["--archive-dir", str(tmp_path / "no-such-archive")])

    assert code == 1
    err = capsys.readouterr().err
    assert "refused" in err
    assert "tracked modifications" in err


def test_cli_refuses_when_git_is_unavailable(tmp_path, monkeypatch, capsys):
    """With no git on PATH, the CLI exits 1 with a message. The run cannot name its
    commits, so it does not run.

    Mutation: make _git return an empty string when git cannot be run.
    """
    no_git = tmp_path / "no-git-here"
    no_git.mkdir()
    monkeypatch.setenv("PATH", str(no_git))

    code = _load_cli().main(["--archive-dir", str(tmp_path / "no-such-archive")])

    assert code == 1
    assert "git is unavailable" in capsys.readouterr().err


def test_cli_exposes_no_manifest_digest_override():
    """The pinned vintage is not a command-line option. The CLI rejects the flag.

    Mutation: add an --expected-manifest-digest option to the CLI.
    """
    with pytest.raises(SystemExit) as exit_info:
        _load_cli().main(
            ["--archive-dir", "unused", "--expected-manifest-digest", EXPECTED_MANIFEST_DIGEST]
        )
    assert exit_info.value.code == 2
