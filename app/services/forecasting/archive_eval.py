"""M3-A evaluation on the archived Open-Meteo Historical Forecast series.

This is a pseudo-real-time evaluation (M2 protocol §2.2) on a fixed vintage of
the Open-Meteo Historical Forecast API, downloaded 2026-10-11. It is NOT the
live series production collects, and results are labelled accordingly.

The archive covers 2018-01-01 through 2026-10-10 for all 21 declared points.
It is close to what was recorded (median |difference| 0.09°C over 23 days of
comparison), but it is a reconstruction from a fixed vintage, not the recording
itself.

**What this evaluation does, in the order it does it:**

- Records provenance (M3-A §3): the commit of the declaration, the commit of the
  code, and the versions of Python, numpy, pandas, scikit-learn and xgboost. A
  tracked modification to the working tree, a shallow clone, or a commit that
  cannot be determined refuses the run before any file is read.
- Reads every weather file once (`weather/<POINT>_<YEAR>.json.gz`): the bytes
  that are hashed are the bytes that are parsed.
- Pins the vintage: the manifest digest must equal EXPECTED_MANIFEST_DIGEST.
- Checks the whole grid before any fit: the 21 declared points times every UTC
  day of the period, each point-day with at least 19 distinct UTC hours (an
  absent day counts as zero), no undeclared or missing point, no duplicated
  (point, hour), no non-finite value, no timestamp off the hour. Anything short
  of that is refused, not filled, and no model is fitted.
- Builds the daily target per M3 §3 / Amendment 1.3 (reusing daily_target.daily_observations)
- Evaluates baselines (Naive, SeasonalNaive, MovingAverage, Climatology) and the
  frozen candidates (FROZEN_CANDIDATES, M3-A §8) over rolling-origin windows
  placed at the END of the series. Each window trains on ALL days from the period
  start to its origin (expanding)
- Reports per-point and macro (equal-weight mean across points) MAE, RMSE, MASE, bias
- Success per M3-A §9: for EVERY declared baseline, the 98.75th percentile of a
  circular block bootstrap of the per-window macro MAE difference is below zero
  (Bonferroni over the family of 4 claims), and the candidate's macro MAE is
  lower than every baseline's. No strongest baseline is selected.

**This is preregistered:** docs/M3A_ARCHIVE_DECLARATION.md was committed before
the first run on this data. Every constant, baseline, candidate, window
placement rule, and success criterion was fixed before any result existed.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import platform
import subprocess
import zlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

from .baselines import ClimatologyForecast, default_baselines
from .daily_target import (
    coverage_by_day,
    daily_observations,
    required_hours,
    snapshot_digest,
)
from .evaluation import EvaluationReport, WindowResult, evaluate_rolling_origin

if TYPE_CHECKING:
    from .challengers import CandidateSpec

ARCHIVE_PERIOD_START = date(2018, 1, 1)
ARCHIVE_PERIOD_END = date(2026, 10, 10)

#: The pinned vintage (M3-A §3): SHA-256 over the sorted lines
#: "<relative path>\t<sha256 of the file>" of the 189 weather files. Any other
#: value is a refusal before any model is fitted. The CLI does not override it.
EXPECTED_MANIFEST_DIGEST = (
    "sha256:2d2ad4c1781102c810b8330adae0e6ad08dc99abed4ece91b21010b08a6b9f49"
)

#: The candidates M3-A §8 freezes, by name, in report order. They are resolved
#: from challengers.CANDIDATES by name; the list is never "every non-shadow entry".
FROZEN_CANDIDATES = ("linear_trend", "xgboost_lag")

HORIZONS = {
    7: {"n_windows": 104, "evaluation_period_start": date(2024, 10, 13)},
    30: {"n_windows": 24, "evaluation_period_start": date(2024, 10, 21)},
}

#: M3-A §9. The family of claims is FROZEN_CANDIDATES x the declared horizons,
#: held at FAMILY_ALPHA by Bonferroni. Each claim is tested at PER_CLAIM_ALPHA.
FAMILY_ALPHA = 0.05
DECLARED_HORIZONS = tuple(sorted(HORIZONS))
DECLARED_FAMILY_SIZE = len(FROZEN_CANDIDATES) * len(DECLARED_HORIZONS)
PER_CLAIM_ALPHA = FAMILY_ALPHA / DECLARED_FAMILY_SIZE

#: M3-A §9, fixed and never tuned.
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 0
#: Below this many windows a failed claim is weak evidence (M3-A §9, power).
POWER_NOTE_BELOW_WINDOWS = 30

RESULT_LABEL = "pseudo-real-time · archive vintage 2026-10-11 · not a real-time claim"
TARGET_NAME = "openmeteo-historical-forecast:temperature_2m"
DECLARATION = "docs/M3A_ARCHIVE_DECLARATION.md v1.2"
DECLARATION_PATH = "docs/M3A_ARCHIVE_DECLARATION.md"

#: The repository root, computed from this file's location
#: (app/services/forecasting/archive_eval.py), never from the process's cwd.
REPO_ROOT = Path(__file__).resolve().parents[3]


class ProvenanceError(RuntimeError):
    """The run cannot name exactly what produced it, so it does not run."""


def _git(args: Sequence[str], cwd: Optional[Path] = None) -> str:
    """Run git and return its stdout, or raise ProvenanceError."""
    where = REPO_ROOT if cwd is None else cwd
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(where),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProvenanceError(f"git is unavailable: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit {result.returncode}"
        raise ProvenanceError(f"git {' '.join(args)} failed in {where}: {detail}")
    return result.stdout


def _library_versions() -> Dict[str, Optional[str]]:
    """Versions of the libraries the figures depend on. None if not installed."""

    def installed(distribution: str) -> Optional[str]:
        try:
            return metadata.version(distribution)
        except metadata.PackageNotFoundError:
            return None

    return {
        "python": platform.python_version(),
        "numpy": installed("numpy"),
        "pandas": installed("pandas"),
        "scikit-learn": installed("scikit-learn"),
        "xgboost": installed("xgboost"),
    }


def collect_provenance(require_clean_tree: bool = True) -> Dict[str, object]:
    """The commit SHAs and library versions a report must carry (M3-A §3).

    `declaration_sha` is the last commit that changed the declaration; `code_sha`
    is HEAD. Raises ProvenanceError if either cannot be determined.

    `require_clean_tree=True` is the official-run mode, and it also refuses a
    shallow clone: there `git log -- <declaration>` returns HEAD, which is the
    last commit that changed the declaration only by coincidence. Tests that run
    in a dirty or shallow checkout pass False and say so.
    """
    declaration_sha = _git(["log", "-1", "--format=%H", "--", DECLARATION_PATH]).strip()
    code_sha = _git(["rev-parse", "HEAD"]).strip()
    if not declaration_sha:
        raise ProvenanceError(f"no commit in this checkout changed {DECLARATION_PATH}")
    if not code_sha:
        raise ProvenanceError("the code commit (HEAD) could not be read")

    if require_clean_tree:
        if _git(["rev-parse", "--is-shallow-repository"]).strip() == "true":
            raise ProvenanceError(
                "shallow clone: the declaration commit cannot be determined here; "
                "run from a full clone"
            )
        dirty = _git(["status", "--porcelain", "--untracked-files=no"]).strip()
        if dirty:
            raise ProvenanceError(
                "working tree has tracked modifications; commit or discard them "
                "before an official run:\n" + dirty
            )

    return {
        "declaration_sha": declaration_sha,
        "code_sha": code_sha,
        "versions": _library_versions(),
    }


def _digest_from_hashes(file_hashes: Sequence[Tuple[str, str]]) -> str:
    """SHA-256 over sorted lines "<relative path>\\t<sha256 of the file>"."""
    canonical = "\n".join(f"{path}\t{fhash}" for path, fhash in sorted(file_hashes))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _point_of(file_path: Path) -> Optional[str]:
    """The point code in `<POINT>_<YEAR>.json.gz`, or None if the name has no year."""
    point, sep, year = file_path.name.removesuffix(".json.gz").rpartition("_")
    if not sep or not point or not year.isdigit():
        return None
    return point


@dataclass
class _WeatherRead:
    """What one pass over the weather files establishes.

    Problems are collected rather than raised, so a refusal can name every one of
    them at once instead of stopping at the first.
    """

    digest: str = ""
    points_in_files: Set[str] = field(default_factory=set)
    rows: List[Tuple[str, datetime, float]] = field(default_factory=list)
    unreadable: List[str] = field(default_factory=list)
    unrecognised: List[str] = field(default_factory=list)
    unparseable: List[str] = field(default_factory=list)
    off_hour: List[str] = field(default_factory=list)
    non_finite: List[str] = field(default_factory=list)
    duplicates: List[str] = field(default_factory=list)


def _read_weather(
    archive_dir: str | Path,
    period_start: date,
    period_end: date,
) -> _WeatherRead:
    """Read every weather file once: hash the bytes, then parse those same bytes.

    `rows` holds the non-null, finite values inside the period. Integrity problems
    are checked on every timestamp in the files, including those outside the
    period, because a corrupt file is corrupt wherever its bad line falls.
    """
    archive_path = Path(archive_dir)
    weather_dir = archive_path / "weather"
    if not weather_dir.exists():
        raise ValueError(f"archive weather directory not found: {weather_dir}")

    read = _WeatherRead()
    file_hashes: List[Tuple[str, str]] = []
    stamp_counts: Counter = Counter()

    for file_path in sorted(weather_dir.glob("*.json.gz")):
        raw = file_path.read_bytes()
        rel_path = file_path.relative_to(archive_path).as_posix()
        file_hashes.append((rel_path, hashlib.sha256(raw).hexdigest()))

        point = _point_of(file_path)
        if point is None:
            read.unrecognised.append(file_path.name)
            continue
        read.points_in_files.add(point)

        try:
            payload = json.loads(gzip.decompress(raw).decode("utf-8"))
            times = payload["hourly"]["time"]
            temps = payload["hourly"]["temperature_2m"]
            if len(times) != len(temps):
                raise ValueError(
                    f"time and temperature_2m lengths differ ({len(times)} vs {len(temps)})"
                )
        except (
            OSError, EOFError, zlib.error, ValueError,
            TypeError, KeyError, AttributeError,
        ) as exc:
            read.unreadable.append(f"{file_path.name}: {exc}")
            continue

        for time_str, temp in zip(times, temps):
            try:
                moment = datetime.fromisoformat(time_str).replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                read.unparseable.append(f"{file_path.name}: {time_str!r}")
                continue

            stamp_counts[(point, int(moment.timestamp()))] += 1
            if moment.minute or moment.second or moment.microsecond:
                read.off_hour.append(f"{point} {time_str}")

            if temp is None:
                continue  # an absent hour, not a value
            try:
                value = float(temp)
            except (TypeError, ValueError):
                read.non_finite.append(f"{point} {time_str}: {temp!r}")
                continue
            if not math.isfinite(value):
                read.non_finite.append(f"{point} {time_str}: {value}")
                continue

            if period_start <= moment.date() <= period_end:
                read.rows.append((point, moment, value))

    duplicated = sorted(
        (point, epoch, count)
        for (point, epoch), count in stamp_counts.items()
        if count > 1
    )
    read.duplicates = [
        f"{point} {datetime.fromtimestamp(epoch, timezone.utc).isoformat()} x{count}"
        for point, epoch, count in duplicated
    ]
    read.digest = _digest_from_hashes(file_hashes)
    return read


def _integrity_refusals(
    read: _WeatherRead,
    declared_points: Sequence[str],
    period_start: date,
    period_end: date,
    expected_digest: str,
) -> Dict[str, object]:
    """Every reason the run must not fit anything (M3-A §3). Empty means it may.

    The grid is the declared points times every UTC day from period_start to
    period_end inclusive. A point-day absent from the files counts as zero hours,
    so an entirely missing year is refused, where a check over the present days
    would have passed it.
    """
    reasons: Dict[str, object] = {}

    if read.digest != expected_digest:
        reasons["manifest_digest"] = {"expected": expected_digest, "actual": read.digest}

    for key, items in (
        ("unreadable_files", read.unreadable),
        ("unrecognised_files", read.unrecognised),
        ("unparseable_timestamps", read.unparseable),
        ("off_hour_timestamps", read.off_hour),
        ("non_finite_values", read.non_finite),
        ("duplicate_point_hours", read.duplicates),
    ):
        if items:
            reasons[key] = {"count": len(items), "first_few": items[:5]}

    declared = set(declared_points)
    undeclared = sorted(point for point in read.points_in_files if point not in declared)
    if undeclared:
        reasons["undeclared_points"] = undeclared
    missing = [point for point in declared_points if point not in read.points_in_files]
    if missing:
        reasons["missing_declared_points"] = missing

    coverage = coverage_by_day(read.rows)
    floor = required_hours()
    grid_days = [
        period_start + timedelta(days=offset)
        for offset in range((period_end - period_start).days + 1)
    ]
    below = [
        f"{point} {day.isoformat()}: {coverage.get((point, day), 0)} h"
        for point in declared_points
        for day in grid_days
        if coverage.get((point, day), 0) < floor
    ]
    if below:
        reasons["point_days_below_floor"] = len(below)
        reasons["first_few_below_floor"] = below[:5]

    observed = sorted({day for _point, day in coverage})
    first = observed[0] if observed else None
    last = observed[-1] if observed else None
    if first != period_start:
        reasons["first_day"] = {
            "expected": period_start.isoformat(),
            "observed": first.isoformat() if first else None,
        }
    if last != period_end:
        reasons["last_day"] = {
            "expected": period_end.isoformat(),
            "observed": last.isoformat() if last else None,
        }
    return reasons


def _resolve_frozen_candidates() -> Tuple[List["CandidateSpec"], List[str]]:
    """The frozen candidates resolved by name, and the names that are missing.

    An entry added to challengers.CANDIDATES is not evaluated unless
    FROZEN_CANDIDATES names it (M3-A §8).
    """
    from . import challengers

    by_name = {spec.name: spec for spec in challengers.CANDIDATES}
    missing = [name for name in FROZEN_CANDIDATES if name not in by_name]
    specs = [by_name[name] for name in FROZEN_CANDIDATES if name in by_name]
    return specs, missing


def load_archive_rows(
    archive_dir: str | Path,
    period_start: date = ARCHIVE_PERIOD_START,
    period_end: date = ARCHIVE_PERIOD_END,
) -> List[Tuple[str, datetime, float]]:
    """Hourly temperature_2m from the weather archive, inside the period.

    Archive layout: `weather/<POINT>_<YEAR>.json.gz`, each a gzipped Open-Meteo
    JSON response with `hourly.time` (ISO "YYYY-MM-DDTHH:MM", UTC) and
    `hourly.temperature_2m`. POINT codes include hyphens (RU-MOW, RU-SPE). Null
    values are absent hours. A file that cannot be read contributes no rows here;
    evaluate_archive refuses such an archive, and this loader does not decide a run.

    Returns:
        List of (point, event_time as tz-aware UTC datetime, value)
    """
    return _read_weather(archive_dir, period_start, period_end).rows


def manifest_digest(archive_dir: str | Path) -> str:
    """SHA-256 over sorted (relative path, sha256 of file bytes) of weather files.

    This fingerprints the archive vintage. A fresh download is a new vintage:
    the provider may revise the archive, and results from two vintages are not
    compared as though they shared one. evaluate_archive does not call this; it
    takes the digest from the same read that parses the files.
    """
    archive_path = Path(archive_dir)
    weather_dir = archive_path / "weather"
    if not weather_dir.exists():
        raise ValueError(f"archive weather directory not found: {weather_dir}")

    file_hashes = [
        (
            file_path.relative_to(archive_path).as_posix(),
            hashlib.sha256(file_path.read_bytes()).hexdigest(),
        )
        for file_path in sorted(weather_dir.glob("*.json.gz"))
    ]
    return _digest_from_hashes(file_hashes)


def _scored_points(
    model: str,
    points: Sequence[str],
    reports_by_point: Dict[str, Dict[str, EvaluationReport]],
) -> List[str]:
    """The declared points on which `model` has a first-pass report."""
    return [p for p in points if p in reports_by_point and model in reports_by_point[p]]


def macro_mae(
    model: str,
    points: Sequence[str],
    reports_by_point: Dict[str, Dict[str, EvaluationReport]],
) -> Optional[float]:
    """Equal-weight mean of per-point MAE, or None if no point was scored."""
    scored = _scored_points(model, points, reports_by_point)
    if not scored:
        return None
    return float(np.mean([reports_by_point[p][model].mean("mae") for p in scored]))


def block_length(n_windows: int) -> int:
    """L = ceil(n ** (1/3)) (M3-A §9), by integer arithmetic.

    The smallest L with L ** 3 >= n. The floating-point cube root is not used,
    because it is inexact at perfect cubes: 64 ** (1/3) is 3.9999999999999996,
    so truncating it gives 3 where the answer is 4.
    """
    if n_windows < 1:
        raise ValueError(f"need at least one window, got {n_windows}")
    length = 1
    while length ** 3 < n_windows:
        length += 1
    return length


def circular_block_resamples(
    n_windows: int,
    n_resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> np.ndarray:
    """Window-index sequences for the circular block bootstrap (M3-A §9).

    Each resample draws ceil(n / L) block starts uniformly from 0..n-1, expands
    every start into L consecutive window indices that wrap modulo n, concatenates
    the blocks and keeps the first n. Returns an int array of shape
    (n_resamples, n_windows). The caller generates it once per horizon and reuses
    it for every candidate-baseline comparison of that horizon.
    """
    length = block_length(n_windows)
    n_blocks = -(-n_windows // length)
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n_windows, size=(n_resamples, n_blocks))
    offsets = np.arange(length)
    indices = (starts[:, :, None] + offsets[None, None, :]) % n_windows
    return indices.reshape(n_resamples, n_blocks * length)[:, :n_windows]


def _window_macro_maes(
    model: str,
    points: Sequence[str],
    reports_by_point: Dict[str, Dict[str, EvaluationReport]],
    n_windows: int,
) -> np.ndarray:
    """Per window, the equal-weight mean over points of the model's window MAE.

    The 21 points stay together inside a window (M3-A §9). Assumes the pairing
    check has already passed, so window k exists in every scored report.
    """
    scored = _scored_points(model, points, reports_by_point)
    return np.array(
        [
            np.mean([reports_by_point[p][model].windows[k].mae for p in scored])
            for k in range(n_windows)
        ],
        dtype=float,
    )


def _window_dates(
    window: WindowResult, calendar: Sequence[date]
) -> Optional[Tuple[date, date, date]]:
    """(train_end, test_start, test_end) of a window as calendar dates.

    WindowResult stores positions. test_end is exclusive there, so the date
    returned for it is the last day tested. None if a position is off the calendar.
    """
    last_test = window.test_end - 1
    positions = (window.train_end, window.test_start, last_test)
    if not all(0 <= p < len(calendar) for p in positions):
        return None
    return (calendar[window.train_end], calendar[window.test_start], calendar[last_test])


def _pairing_problem(
    candidate: str,
    baselines: Sequence[str],
    points: Sequence[str],
    reports_by_point: Dict[str, Dict[str, EvaluationReport]],
    n_windows: int,
    calendar: Sequence[date],
) -> Optional[str]:
    """The first way the candidate and baselines fail to pair, or None (M3-A §9).

    Every (model, point) report must hold exactly n_windows windows, every window
    MAE must be finite, and window k must cover the same dates in every report.
    The reference for window k is the candidate on the first declared point.
    """
    reference: Dict[int, Tuple[Tuple[date, date, date], str]] = {}
    for model in [candidate, *baselines]:
        for point in points:
            report = reports_by_point[point][model]
            if len(report.windows) != n_windows:
                return f"{model}/{point}: {len(report.windows)} windows, expected {n_windows}"
            for k, window in enumerate(report.windows):
                label = f"{model}/{point} window {k}"
                if window.window != k:
                    return f"{label} is labelled {window.window}"
                if not math.isfinite(window.mae):
                    return f"{label} has a non-finite MAE ({window.mae})"
                dates = _window_dates(window, calendar)
                if dates is None:
                    return f"{label} lies outside the calendar"
                if k not in reference:
                    reference[k] = (dates, label)
                    continue
                expected, reference_label = reference[k]
                if dates != expected:
                    return (
                        f"{label} covers {_format_dates(dates)}, but {reference_label} "
                        f"covers {_format_dates(expected)}"
                    )
    return None


def _format_dates(dates: Tuple[date, date, date]) -> str:
    return "(" + ", ".join(day.isoformat() for day in dates) + ")"


def candidate_verdict(
    candidate: str,
    baselines: Sequence[str],
    reports_by_point: Dict[str, Dict[str, EvaluationReport]],
    points: Sequence[str],
    n_windows: int,
    resample_indices: np.ndarray,
    calendar: Sequence[date],
    per_claim_alpha: float = PER_CLAIM_ALPHA,
    failures_by_point: Optional[Dict[str, Dict[str, str]]] = None,
) -> Dict[str, object]:
    """Verdict for one candidate at one horizon, per M3-A section 9.

    Pure: it reads the first-pass EvaluationReports and never refits.

    A claim succeeds only if, for EVERY baseline b, the one-sided upper bound of
    the circular-block-bootstrap distribution of mean(d_w) is below zero, where
    d_w is the candidate's macro MAE minus b's macro MAE in window w, at the
    per-claim level (98.75th percentile by default). It also requires the
    candidate's macro MAE to be strictly lower than every baseline's; the bound
    already implies this, and the check is kept explicit. No strongest baseline
    is selected. The resamples are generated by the caller, once per horizon.

    Returns not_comparable, with the reason, if the candidate, a baseline or a
    point is missing, or if the reports do not pair (window count, dates, finite
    MAE).
    """
    if not baselines:
        raise ValueError("a verdict needs at least one baseline")
    resample_indices = np.asarray(resample_indices)
    if resample_indices.ndim != 2 or resample_indices.shape[1] != n_windows:
        raise ValueError(
            f"resample_indices must be (resamples, {n_windows}), got {resample_indices.shape}"
        )
    if not 0.0 < per_claim_alpha < 1.0:
        raise ValueError(f"per_claim_alpha must be in (0, 1), got {per_claim_alpha}")

    failures_by_point = failures_by_point or {}
    scored = {
        model: _scored_points(model, points, reports_by_point)
        for model in [candidate, *baselines]
    }

    if not scored[candidate]:
        return {"succeeded": False, "reason": "candidate could not be scored"}

    # If ANY baseline could not be scored for ANY point, verdict is not_comparable
    missing_baseline_points: List[Tuple[str, str, str]] = []
    for baseline_name in baselines:
        for point in points:
            if point not in scored[baseline_name]:
                reason = failures_by_point.get(point, {}).get(baseline_name, "unknown")
                missing_baseline_points.append((baseline_name, point, reason))

    if missing_baseline_points:
        missing_summary = [f"{bl}/{pt}: {rsn}" for bl, pt, rsn in missing_baseline_points[:5]]
        return {
            "succeeded": False,
            "reason": "not_comparable",
            "explanation": "not all declared baselines scored for all declared points",
            "missing_baseline_point_pairs": len(missing_baseline_points),
            "missing": missing_summary,
        }

    cand_points = set(scored[candidate])
    baseline_point_sets = {bl: set(scored[bl]) for bl in baselines}
    all_compared_models = [candidate] + list(baselines)
    common_points = cand_points.intersection(*baseline_point_sets.values())

    if len(common_points) < len(points):
        missing_for_any = []
        for point in points:
            for model in all_compared_models:
                if point not in scored[model]:
                    reason = failures_by_point.get(point, {}).get(model, "unknown")
                    missing_for_any.append(f"{point}/{model}: {reason}")
                    break

        return {
            "succeeded": False,
            "reason": "not_comparable",
            "explanation": "not all declared points scored for all compared models",
            "missing": missing_for_any[:5],
            "points_scored": len(common_points),
            "points_declared": len(points),
        }

    pairing_problem = _pairing_problem(
        candidate, baselines, points, reports_by_point, n_windows, calendar
    )
    if pairing_problem is not None:
        return {
            "succeeded": False,
            "reason": "not_comparable",
            "explanation": "the reports do not pair window for window",
            "pairing": pairing_problem,
        }

    if n_windows < 2:
        return {
            "succeeded": False,
            "reason": "not enough windows for bootstrap",
        }

    cand_windows = _window_macro_maes(candidate, points, reports_by_point, n_windows)
    cand_mae = float(cand_windows.mean())
    baseline_windows = {
        bl: _window_macro_maes(bl, points, reports_by_point, n_windows) for bl in baselines
    }
    baseline_maes = {bl: float(windows.mean()) for bl, windows in baseline_windows.items()}
    beats_every_baseline = all(cand_mae < mae for mae in baseline_maes.values())

    bound_percentile = round(100.0 * (1.0 - per_claim_alpha), 6)
    per_baseline: Dict[str, Dict[str, object]] = {}
    for baseline_name in baselines:
        diffs = cand_windows - baseline_windows[baseline_name]
        bootstrap_means = diffs[resample_indices].mean(axis=1)
        upper = float(np.percentile(bootstrap_means, bound_percentile))
        per_baseline[baseline_name] = {
            "mean_difference": float(diffs.mean()),
            "upper_bound": upper,
            "upper_bound_percentile": bound_percentile,
            "passes": bool(upper < 0.0),
        }

    succeeded = beats_every_baseline and all(
        entry["passes"] for entry in per_baseline.values()
    )

    block = block_length(n_windows)
    verdict: Dict[str, object] = {
        "succeeded": bool(succeeded),
        "beats_every_baseline": bool(beats_every_baseline),
        "per_baseline": per_baseline,
        "per_claim_alpha": per_claim_alpha,
        "candidate_mae": cand_mae,
        "baseline_maes": baseline_maes,
        "n_windows": n_windows,
        "block_length": block,
        "n_blocks": -(-n_windows // block),
        "resamples": int(resample_indices.shape[0]),
    }
    if n_windows < POWER_NOTE_BELOW_WINDOWS:
        verdict["power_note"] = (
            f"{n_windows} windows in {-(-n_windows // block)} blocks: "
            "a failure here is weak evidence that no effect exists"
        )
    return verdict



def evaluate_archive(
    archive_dir: str | Path,
    horizons: Optional[Sequence[int]] = None,
    window_configs: Optional[Dict[int, Dict]] = None,
    *,
    expected_manifest_digest: str = EXPECTED_MANIFEST_DIGEST,
    period_start: date = ARCHIVE_PERIOD_START,
    period_end: date = ARCHIVE_PERIOD_END,
    require_clean_tree: bool = True,
) -> Dict[str, object]:
    """Run the preregistered M3-A evaluation on the archive.

    This is the evaluation logic, testable without network or database. The CLI
    script is a thin wrapper around it, and calls it with the defaults only.

    Args:
        archive_dir: Path to the archive (contains weather/ subdirectory)
        horizons: Which horizons to evaluate (defaults to [7, 30])
        window_configs: Optional custom window configurations (defaults to HORIZONS).
                       Format: {horizon: {"n_windows": int, "evaluation_period_start": date}}
        expected_manifest_digest: The pinned vintage. Tests pass their synthetic
                       archive's digest; the CLI uses the constant.
        period_start, period_end: The grid bounds, inclusive. Tests pass their own;
                       the CLI uses the declared period.
        require_clean_tree: Refuse a tracked modification and a shallow clone
                       (see collect_provenance). Tests run in a dirty tree and pass False.

    Returns:
        A deterministic report dict (apart from generated_at timestamp) with:
        - label, declaration, target, provenance (declaration_sha, code_sha, versions)
        - manifest digest (actual and expected), snapshot digest
        - period, points, day counts
        - refused=True with refusal_reasons, and no results, if any check fails
        - otherwise per-horizon results and success verdicts per candidate

    Raises:
        ProvenanceError: git is unavailable, a commit cannot be determined, or
            require_clean_tree is set and the run is not from a clean full clone.
    """
    horizons = list(horizons) if horizons is not None else list(DECLARED_HORIZONS)
    window_configs = window_configs if window_configs is not None else HORIZONS
    if period_end < period_start:
        raise ValueError(f"period end {period_end} is before period start {period_start}")

    provenance = collect_provenance(require_clean_tree=require_clean_tree)
    frozen_specs, missing_candidates = _resolve_frozen_candidates()

    read = _read_weather(archive_dir, period_start, period_end)

    from app.ingesters.openmeteo import REGION_CAPITALS
    declared_points = sorted(code for code, _lat, _lon in REGION_CAPITALS)

    refusals = _integrity_refusals(
        read, declared_points, period_start, period_end, expected_manifest_digest
    )
    if missing_candidates:
        refusals["missing_frozen_candidates"] = missing_candidates

    observations = daily_observations(read.rows)
    days_set = sorted({o.period_end for o in observations})

    report: Dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "label": RESULT_LABEL,
        "declaration": DECLARATION,
        "declaration_sha": provenance["declaration_sha"],
        "code_sha": provenance["code_sha"],
        "versions": provenance["versions"],
        "target": TARGET_NAME,
        "candidates": list(FROZEN_CANDIDATES),
        "manifest_digest": read.digest,
        "expected_manifest_digest": expected_manifest_digest,
        "snapshot_digest": snapshot_digest(read.rows),
        "period": {
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
            "days": (period_end - period_start).days + 1,
        },
        "points": {
            "declared": declared_points,
            "count": len(declared_points),
        },
        "day_count": len(days_set),
        "family": {
            "alpha": FAMILY_ALPHA,
            "claims_declared": DECLARED_FAMILY_SIZE,
            "claims_in_this_run": len(FROZEN_CANDIDATES) * len(horizons),
            "per_claim_alpha": PER_CLAIM_ALPHA,
            "horizons_declared": list(DECLARED_HORIZONS),
            "horizons_run": horizons,
            "official": horizons == list(DECLARED_HORIZONS),
        },
    }

    if refusals:
        report["refused"] = True
        report["refusal_reasons"] = refusals
        return report

    report["refused"] = False

    # The grid check passed, so every point has a daily value on every day of the
    # period, and row positions are calendar days. The window arithmetic relies on it.
    expected_day_count = (period_end - period_start).days + 1
    if len(days_set) != expected_day_count or days_set[0] != period_start:
        raise RuntimeError("grid check passed but the daily series is not the grid")

    results_by_horizon: Dict[int, Dict] = {}

    for horizon in horizons:
        config = window_configs.get(horizon)
        if config is None:
            raise ValueError(f"horizon {horizon} not in window configuration")

        n_windows = config["n_windows"]
        n_days = len(days_set)
        # Generated once per horizon, shared by every claim of that horizon (M3-A §9).
        resample_indices = circular_block_resamples(n_windows)

        train_size = n_days - horizon * n_windows
        if train_size < 1:
            raise ValueError(
                f"h={horizon}: {n_days} days cannot support {n_windows} "
                f"windows (need >= {horizon * n_windows + 1})"
            )

        first_test_idx = train_size
        computed_first_test = days_set[first_test_idx]
        declared_first_test = config["evaluation_period_start"]
        if computed_first_test != declared_first_test:
            raise ValueError(
                f"h={horizon}: computed first test day {computed_first_test.isoformat()} "
                f"!= declared {declared_first_test.isoformat()}"
            )

        baselines = default_baselines(season_length=7, window=7) + [ClimatologyForecast()]

        candidates_to_run = frozen_specs

        per_point_reports: Dict[str, Dict[str, EvaluationReport]] = {}
        per_point_failures: Dict[str, Dict[str, str]] = {}

        for point in declared_points:
            point_obs = [o for o in observations if o.region == point]
            frame = pd.DataFrame([
                {"ds": pd.Timestamp(o.period_end), "y": o.value}
                for o in point_obs
            ]).sort_values("ds").reset_index(drop=True)

            if len(frame) < train_size + horizon:
                for model in [b.model_name for b in baselines] + [c.name for c in candidates_to_run]:
                    per_point_failures.setdefault(point, {})[model] = (
                        f"insufficient data: {len(frame)} rows, need {train_size + horizon}"
                    )
                continue

            point_reports: Dict[str, EvaluationReport] = {}
            point_failures: Dict[str, str] = {}

            for baseline in baselines:
                def fit_predict(train_df, h, _model=baseline):
                    _model.fit(train_df, "y")
                    return _model.predict(h).yhat

                try:
                    report_obj = evaluate_rolling_origin(
                        fit_predict, frame, "y",
                        horizon=horizon, train_size=train_size,
                        n_windows=n_windows, season_length=7,
                        model_name=baseline.model_name,
                    )
                    point_reports[baseline.model_name] = report_obj
                except ValueError as exc:
                    point_failures[baseline.model_name] = str(exc)

            for spec in candidates_to_run:
                model = spec.factory()

                def fit_predict_cand(train_df, h, _model=model):
                    _model.fit(train_df, "y")
                    return _model.predict(h).yhat

                try:
                    report_obj = evaluate_rolling_origin(
                        fit_predict_cand, frame, "y",
                        horizon=horizon, train_size=train_size,
                        n_windows=n_windows, season_length=7,
                        model_name=spec.name,
                    )
                    point_reports[spec.name] = report_obj
                except ValueError as exc:
                    point_failures[spec.name] = str(exc)

            per_point_reports[point] = point_reports
            per_point_failures[point] = point_failures

        all_models = {b.model_name for b in baselines} | {c.name for c in candidates_to_run}

        points_scored_per_model: Dict[str, List[str]] = {}
        for model in all_models:
            scored = [
                point for point in declared_points
                if point in per_point_reports and model in per_point_reports[point]
            ]
            points_scored_per_model[model] = scored

        per_point_metrics: Dict[str, Dict[str, Dict]] = {}
        for point in declared_points:
            point_metrics: Dict[str, Dict] = {}
            for model in all_models:
                if point in per_point_reports and model in per_point_reports[point]:
                    rep = per_point_reports[point][model]
                    point_metrics[model] = {
                        "mae": rep.mean("mae"),
                        "rmse": rep.mean("rmse"),
                        "mase": rep.mean("mase"),
                        "bias": rep.mean("bias"),
                    }
            per_point_metrics[point] = point_metrics

        macro_metrics: Dict[str, Dict] = {}
        for model in all_models:
            if points_scored_per_model[model]:
                macro_metrics[model] = {
                    "mae": macro_mae(model, declared_points, per_point_reports),
                    "rmse": float(np.mean([
                        per_point_reports[point][model].mean("rmse")
                        for point in points_scored_per_model[model]
                    ])),
                    "mase": float(np.mean([
                        per_point_reports[point][model].mean("mase")
                        for point in points_scored_per_model[model]
                    ])),
                    "bias": float(np.mean([
                        per_point_reports[point][model].mean("bias")
                        for point in points_scored_per_model[model]
                    ])),
                    "points_scored": len(points_scored_per_model[model]),
                }

        baseline_names = [b.model_name for b in baselines]
        candidate_verdicts: Dict[str, Dict] = {}

        for spec in candidates_to_run:
            candidate_verdicts[spec.name] = candidate_verdict(
                spec.name,
                baseline_names,
                per_point_reports,
                declared_points,
                n_windows,
                resample_indices,
                days_set,
                per_claim_alpha=PER_CLAIM_ALPHA,
                failures_by_point=per_point_failures,
            )

        failure_summary = {}
        for model in all_models:
            failures = []
            for point in declared_points:
                if point in per_point_failures and model in per_point_failures[point]:
                    failures.append(f"{point}: {per_point_failures[point][model]}")
            if failures:
                failure_summary[model] = {"count": len(failures), "first_few": failures[:5]}

        results_by_horizon[horizon] = {
            "n_windows": n_windows,
            "train_size": train_size,
            "first_test_day": computed_first_test.isoformat(),
            "per_point_metrics": per_point_metrics,
            "macro_metrics": macro_metrics,
            "candidate_verdicts": candidate_verdicts,
            "bootstrap": {
                "scheme": "circular block",
                "block_length": block_length(n_windows),
                "n_blocks": -(-n_windows // block_length(n_windows)),
                "resamples": BOOTSTRAP_RESAMPLES,
                "seed": BOOTSTRAP_SEED,
            },
            "failures": failure_summary,
        }

    report["results_by_horizon"] = results_by_horizon
    return report
