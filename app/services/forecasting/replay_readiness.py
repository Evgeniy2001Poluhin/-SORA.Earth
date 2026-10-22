"""Offline design checks for a historical forecast replay; never a skill gate.

This checks supplied metadata, not the authenticity of receipts or weather bytes.
It neither reads a weather archive nor fits, scores or promotes any model.
Even a passing plan still needs independent data/provenance and protocol review.
The existing live M3 and M3-A declarations are deliberately not imported/changed.
"""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_LIVE_CLAIM = "Live M3 eligibility and forecast skill are not established."


class ReplayPlanError(ValueError):
    """The supplied plan does not describe an admissible replay design."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ReplayPlanError(reason)


def _text(value: object, name: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), f"{name}: missing text")
    _require(not any(ord(c) < 32 or ord(c) == 127 for c in value),
             f"{name}: control character")
    return value


def _moment(value: object, name: str) -> datetime:
    _require(isinstance(value, str), f"{name}: expected UTC ISO timestamp")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReplayPlanError(f"{name}: invalid timestamp") from exc
    _require(moment.tzinfo is not None and moment.utcoffset() == timedelta(0),
             f"{name}: explicit UTC offset required")
    return moment.astimezone(timezone.utc)


def _digest(value: object, name: str) -> None:
    _require(isinstance(value, str) and _SHA256.fullmatch(value) is not None,
             f"{name}: expected lowercase SHA-256")


def _unique_texts(value: object, name: str) -> list[str]:
    _require(isinstance(value, list) and bool(value), f"{name}: nonempty list required")
    result = [_text(item, name) for item in value]
    _require(len(set(result)) == len(result), f"{name}: duplicate item")
    return result


def expected_hours(origin: datetime, horizon_days: int) -> set[datetime]:
    """All hours of the next h complete UTC days, not just the h-th lead."""
    try:
        first_day = origin.date() + timedelta(days=1)
        start = datetime.combine(first_day, time(), tzinfo=timezone.utc)
        return {start + timedelta(hours=i) for i in range(24 * horizon_days)}
    except OverflowError as exc:
        raise ReplayPlanError("origin: horizon exceeds the timestamp range") from exc


def check_replay_plan(plan: object) -> dict:
    """Fail closed on metadata leakage/misalignment; return design-review status.

    Each point/origin must use one run for the whole horizon. Initialisation is
    NOT availability. Original-run replay requires declared historical receipts.
    A reconstructed vintage can only be exploratory, explicitly pseudo-real-time;
    it never inherits the original-run availability claim. Receipt authenticity
    is outside this offline check in either mode.
    """
    _require(isinstance(plan, dict), "plan: JSON object required")
    _require(type(plan.get("schema_version")) is int and plan["schema_version"] == 1,
             "schema_version: expected 1")
    role = plan.get("study_role")
    _require(role in ("exploratory", "confirmatory"), "study_role: unsupported")
    exposure = plan.get("outcome_exposure")
    _require(exposure in ("seen", "unseen", "unknown"), "outcome_exposure: unsupported")
    _require(role != "confirmatory" or exposure == "unseen",
             "confirmatory: exposed or unknown outcomes cannot be a fresh holdout")
    source_kind = plan.get("source_kind")
    _require(source_kind in ("operational_archive", "retrospective_reconstruction"),
             "source_kind: explicitly declare original runs or a reconstructed vintage")
    retrospective = source_kind == "retrospective_reconstruction"
    _require(not retrospective or role == "exploratory",
             "reconstruction: this preflight only supports an exploratory design")
    basis = "retrieved_vintage_only" if retrospective else "contemporaneous_receipt"
    _require(plan.get("availability_basis") == basis,
             "availability_basis: declare receipts or explicitly retrospective retrieval; no assumed lag")
    for name in ("forecast_snapshot_sha256", "verification_snapshot_sha256",
                 "availability_evidence_sha256", "exposure_log_sha256"):
        _digest(plan.get(name), name)
    for name in ("provider", "model", "model_version", "verification_target"):
        _text(plan.get(name), name)
    _require(plan.get("target") == "temperature_2m_daily_mean_utc",
             "target: this design is daily mean temperature in UTC")
    _require(plan.get("temperature_unit") == "degC", "temperature_unit: require degC")
    horizon = plan.get("horizon_days")
    _require(type(horizon) is int and horizon in (7, 30), "horizon_days: require 7 or 30")
    points = _unique_texts(plan.get("points"), "points")
    _require(len(points) <= 21, "points: metadata preflight supports at most 21")
    origin_values = _unique_texts(plan.get("origins"), "origins")
    _require(len(origin_values) <= 520, "origins: metadata preflight supports at most 520")
    origins = [_moment(value, "origins") for value in origin_values]
    _require(len(set(origins)) == len(origins), "origins: duplicate instant")
    _require(origins == sorted(origins), "origins: expected chronological order")
    grids = {origin: expected_hours(origin, horizon) for origin in origins}
    for earlier, later in zip(origins, origins[1:]):
        _require(max(grids[earlier]) < min(grids[later]), "origins: test windows overlap")

    runs = plan.get("runs")
    _require(isinstance(runs, list) and bool(runs), "runs: nonempty list required")
    _require(len(runs) <= 21 * 520, "runs: metadata preflight size limit exceeded")
    expected = {(point, origin) for point in points for origin in origins}
    seen = set()
    for run in runs:
        _require(isinstance(run, dict), "run: JSON object required")
        point = _text(run.get("point"), "run.point")
        origin = _moment(run.get("origin"), "run.origin")
        key = (point, origin)
        _require(key in expected, "run: undeclared point or origin")
        _require(key not in seen, "run: duplicate point/origin")
        seen.add(key)
        _require(run.get("model_version") == plan["model_version"],
                 "run: mixed model versions need a separately declared segment")
        init = _moment(run.get("initialised_at"), "run.initialised_at")
        available = _moment(run.get("available_at"), "run.available_at")
        fetched = _moment(run.get("retrieved_at"), "run.retrieved_at")
        _require(init <= origin, "run: initialisation after forecast origin")
        _require(init <= available and (retrospective or available <= origin),
                 "run: not available at forecast origin")
        _require(available <= fetched, "run: retrieval precedes claimed availability")
        _digest(run.get("payload_sha256"), "run.payload_sha256")
        _digest(run.get("availability_receipt_sha256"), "run.availability_receipt_sha256")
        hour_values = _unique_texts(run.get("valid_hours"), "run.valid_hours")
        _require(len(hour_values) <= 24 * horizon + 1, "run.valid_hours: size limit exceeded")
        hours = [_moment(value, "run.valid_hours") for value in hour_values]
        _require(len(set(hours)) == len(hours), "run.valid_hours: duplicate instant")
        _require(set(hours) == grids[origin],
                 "run: require every UTC hour of the whole horizon from this one run")
    _require(seen == expected, "runs: missing declared point/origin; do not drop hard cases")
    return {
        "status": "metadata_consistent_requires_independent_review",
        "study_role": role,
        "result_label": (
            "pseudo-real-time reconstructed vintage; not a real-time claim; not live M3"
            if retrospective else "archived operational forecast replay; not live M3"
        ),
        "points": len(points),
        "windows": len(origins),
        "horizon_days": horizon,
        "forecast_hours": len(runs) * 24 * horizon,
        "authenticity_verified": False,
        "outcome_coverage_verified": False,
        "skill_evaluated": False,
        "promotion_allowed": False,
        "limitation": _LIVE_CLAIM,
    }
