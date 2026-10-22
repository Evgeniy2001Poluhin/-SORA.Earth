"""Synthetic-tested candidate for a retrospective, event-time-only experiment.

No IO, acquisition, model promotion or live forecast integration. Completed
target days are NOT evidence that a historical reanalysis was available then.
The caller must separately pin/validate the source bytes and homogeneous vintage.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable

from app.services.environmental.regions import OPENMETEO_POINTS_V1

POINTS = frozenset(point.code for point in OPENMETEO_POINTS_V1)
WINDOW_DAYS = 365
MIN_PAIRS = 180
MAX_INPUT_PAIRS = 200_000
LABEL = "retrospective event-time-only calibration; not historical availability; not live M3"


class CalibrationRefusal(ValueError):
    """Invalid or ambiguous inputs; do not silently discard offending rows."""


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise CalibrationRefusal(message)


def _cutoff(value: datetime) -> None:
    _need(isinstance(value, datetime) and value.utcoffset() == timedelta(0),
          "cutoff must be explicitly UTC")
    _need((value.hour, value.minute, value.second, value.microsecond) == (23, 59, 59, 0),
          "cutoff must be exactly 23:59:59 UTC")


def _cell(point_code: str, lead_day: int) -> None:
    _need(isinstance(point_code, str) and point_code in POINTS, "unknown point")
    _need(type(lead_day) is int and 1 <= lead_day <= 7, "lead must be integer 1..7")


def _number(value: float) -> None:
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        valid = False
    _need(valid, "finite number required")


@dataclass(frozen=True)
class CalibrationPair:
    point_code: str
    issued_at: datetime
    lead_day: int
    forecast_c: float
    actual_c: float

    def __post_init__(self) -> None:
        _cell(self.point_code, self.lead_day)
        _cutoff(self.issued_at)
        _number(self.forecast_c)
        _number(self.actual_c)
        _number(self.actual_c - self.forecast_c)
        # Validate arithmetic near datetime limits now, not halfway through fit.
        try:
            _ = self.target_end
        except OverflowError as exc:
            raise CalibrationRefusal("target day outside supported calendar") from exc

    @property
    def target_day(self) -> date:
        return self.issued_at.date() + timedelta(days=self.lead_day)

    @property
    def target_end(self) -> datetime:
        """Exclusive end of the complete UTC target day, NOT publication time."""
        return datetime.combine(self.target_day + timedelta(days=1), time(), timezone.utc)

    @property
    def key(self) -> tuple[str, datetime, int]:
        return self.point_code, self.issued_at, self.lead_day


@dataclass(frozen=True)
class BiasCorrection:
    point_code: str
    lead_day: int
    origin: datetime
    status: str
    eligible_pairs: int
    correction_c: float
    first_issue: datetime | None
    last_issue: datetime | None
    last_target_end: datetime | None
    selected_keys_sha256: str
    label: str = LABEL

    def apply(self, raw_forecast_c: float) -> float:
        _number(raw_forecast_c)
        value = raw_forecast_c + self.correction_c
        _number(value)
        return value


def fit_correction(
    pairs: Iterable[CalibrationPair], *, point_code: str, lead_day: int, origin: datetime,
) -> BiasCorrection:
    """Mean(actual-forecast) in a fixed past issuance window and completed targets.

    Refuse malformed/duplicate rows even outside the requested cell or window.
    This catches ambiguous cache ingestion rather than selecting convenient rows.
    Insufficient eligible history is an explicit uncalibrated fallback, not an
    estimated zero bias or a claim that the calibrated candidate passed readiness.
    """
    _cell(point_code, lead_day)
    _cutoff(origin)
    try:
        lower = origin - timedelta(days=WINDOW_DAYS)
    except OverflowError as exc:
        raise CalibrationRefusal("origin cannot support calibration window") from exc
    selected, seen = [], set()
    for count, pair in enumerate(pairs, start=1):
        _need(count <= MAX_INPUT_PAIRS, "input pair budget exceeded")
        _need(isinstance(pair, CalibrationPair), "expected validated CalibrationPair")
        _need(pair.key not in seen, "duplicate point/issue/lead")
        seen.add(pair.key)
        if (pair.point_code == point_code and pair.lead_day == lead_day and
                lower <= pair.issued_at < origin and pair.target_end <= origin):
            selected.append(pair)
    selected.sort(key=lambda pair: pair.issued_at)
    keys = [(pair.point_code, pair.issued_at.isoformat(), pair.lead_day) for pair in selected]
    key_hash = hashlib.sha256(json.dumps(keys, separators=(",", ":")).encode()).hexdigest()
    status, correction = "raw_fallback_insufficient_history", 0.0
    if len(selected) >= MIN_PAIRS:
        status = "calibrated"
        try:
            correction = math.fsum((pair.actual_c - pair.forecast_c) / len(selected)
                                   for pair in selected)
        except OverflowError as exc:
            raise CalibrationRefusal("nonfinite correction") from exc
        _number(correction)
    return BiasCorrection(
        point_code, lead_day, origin, status, len(selected), correction,
        selected[0].issued_at if selected else None,
        selected[-1].issued_at if selected else None,
        max((pair.target_end for pair in selected), default=None), key_hash,
    )
