"""Calendar and conservative quota planning only: no IO, HTTP, fitting or scoring."""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from fractions import Fraction
from urllib.parse import urlencode

from app.services.environmental.regions import OPENMETEO_POINTS_V1

START = date(2024, 4, 1)  # After the advertised March2024 archive start; not a coverage guarantee.
HORIZON = 7
WINDOW = 365
MIN_PAIRS = 180
POINT_COUNT = len(OPENMETEO_POINTS_V1)
DAY_CAP = 8_000
HOUR_CAP = 4_000
MINUTE_CAP = 400
SHARED_RESERVE = 1_000  # Assumed allowance, not measured provider/IP usage.
P0_MANIFEST_PIN = "76532ea73018373253d8e4c5415542521cd55e2d67e3c1cb8f09703ad300184f"
P0_PROTOCOL_PIN = "f1e6b4d28ef8a9e523c9d5a9cad8f2f219c2cf38c93b5f315be58dc7b7d12700"


def request_weight(*, locations: int, days: int) -> Fraction:
    """One model/variable only. Floor of one per location is a conservative assumption.

    Pricing documents long-query weighting, but does not fully specify rounding
    for every endpoint. This is a reservation, never a measured billing receipt.
    """
    if type(locations) is not int or locations < 1 or type(days) is not int or days < 1:
        raise ValueError("positive integer locations/days required")
    return locations * max(Fraction(1), Fraction(days, 14))


def cutoff(day: date) -> datetime:
    return datetime.combine(day, time(23, 59, 59), timezone.utc)


def origins() -> tuple[datetime, ...]:
    return tuple(datetime(2025, month, 1, tzinfo=timezone.utc) - timedelta(seconds=1)
                 for month in range(1, 13))


def eligible(issue: date, origin: datetime, lead: int) -> bool:
    target_end = datetime.combine(issue + timedelta(days=lead + 1), time(), timezone.utc)
    return origin - timedelta(days=WINDOW) <= cutoff(issue) < origin and target_end <= origin


def reserve_batches(count: int, *, prior: int, reserve: int = SHARED_RESERVE) -> list[dict]:
    """Planning allotments only; a runner still needs persisted rolling counters.

    No retries are included. Failed attempts spend quota; do not refill or reset
    allotments from these numbers. Extra traffic above reserve invalidates the plan.
    """
    if any(type(v) is not int or v < 0 for v in (count, prior, reserve)) or reserve >= DAY_CAP:
        raise ValueError("invalid quota inputs")
    batches, remaining, day = [], count, 0
    weight = int(request_weight(locations=POINT_COUNT, days=8))
    while remaining:
        available = max(0, DAY_CAP - reserve - (prior if day == 0 else 0))
        take = min(remaining, available // weight)
        hour_counts, left = [], take
        while left:
            block = min(left, HOUR_CAP // weight)
            hour_counts.append(block)
            left -= block
        batches.append({"utc_day_offset": day, "http_attempts": take,
                        "reserved_weight": take * weight, "hour_allotments": hour_counts,
                        "prior_weight_allowance": prior if day == 0 else 0,
                        "other_traffic_allowance": reserve})
        remaining -= take
        day += 1
    return batches


def _request(day: date) -> dict:
    params = {
        "latitude": ",".join(str(p.lat) for p in OPENMETEO_POINTS_V1),
        "longitude": ",".join(str(p.lon) for p in OPENMETEO_POINTS_V1),
        "hourly": "temperature_2m", "temperature_unit": "celsius", "timezone": "UTC",
        "timeformat": "iso8601", "cell_selection": "land", "models": "ecmwf_ifs",
        "run": day.isoformat() + "T00:00", "forecast_days": 8,
    }
    return {"issue_date": day.isoformat(), "origin": cutoff(day).isoformat(),
            "file": f"ifs-{day.isoformat()}.json",
            "url": "https://single-runs-api.open-meteo.com/v1/forecast?" + urlencode(params),
            "reserved_weight": int(request_weight(locations=POINT_COUNT, days=8))}


def build_plan() -> dict:
    eval_origins = origins()
    last = eval_origins[-1].date() - timedelta(days=2)  # Latest mature lead1 issue.
    days = [START + timedelta(days=i) for i in range((last - START).days + 1)]
    first_last = eval_origins[0].date() - timedelta(days=HORIZON + 1)
    first = [first_last - timedelta(days=MIN_PAIRS - 1 - i) for i in range(MIN_PAIRS)]
    assert set(first) <= set(days)
    existing = {origin.date(): index for index, origin in enumerate(eval_origins)
                if origin.date() in days}
    new_dates = [*first, *(day for day in days if day not in set(first) and day not in existing)]
    assert len(new_dates) == len(set(new_dates))
    counts = []
    for origin in eval_origins:
        counts.append({"origin": origin.isoformat(), "per_point_eligible_by_lead": {
            str(h): sum(eligible(day, origin, h) for day in days) for h in range(1, 8)}})
    verification_days = (date(2025, 12, 7) - date(2022, 1, 1)).days + 1
    verification_weight = request_weight(locations=POINT_COUNT, days=verification_days)
    p0_weight = verification_weight + 12 * request_weight(locations=POINT_COUNT, days=8)
    # One previously reported single-point access probe, before P0, reserved too.
    prior = math.ceil(p0_weight + request_weight(locations=1, days=8))
    requests = [_request(day) for day in new_dates]
    return {
        "id": "M3-P1-CACHE-FEASIBILITY-2026-10-22", "scope": "plan and three forecast-only probes",
        "role": "exploratory; no real calibration or quality scoring",
        "p0_protocol_sha256": P0_PROTOCOL_PIN, "reuse_manifest_sha256": P0_MANIFEST_PIN,
        "point_count": POINT_COUNT, "start_issue_date": START.isoformat(),
        "end_issue_date": last.isoformat(), "daily_calendar_runs": len(days),
        "coverage_status": "structural potential only; not downloaded or provider-verified",
        "potential_counts_same_for_each_declared_point": counts,
        "reuse_candidates": [{"issue_date": day.isoformat(), "file": f"forecast-{index:02d}.json"}
                             for day, index in sorted(existing.items())],
        "reuse_requires": "verify existing manifest pin, exact URLs/bytes and complete point/hour grids",
        "new_runs_if_reuse_verified": len(requests),
        "new_weight_if_reuse_verified": len(requests) * POINT_COUNT,
        "new_weight_without_reuse": len(days) * POINT_COUNT,
        "stage_one": {"origin": eval_origins[0].isoformat(), "runs": len(first),
                      "issue_start": first[0].isoformat(), "issue_end": first[-1].isoformat(),
                      "potential_pairs_per_point_and_lead": MIN_PAIRS,
                      "reserved_weight": MIN_PAIRS * POINT_COUNT,
                      "note": "cache feasibility slice, not the full twelve-window experiment"},
        "probe_requests": [requests[index] for index in (0, 89, 179)],
        "probe_policy": {"max_http_attempts": 3, "retries": 0, "max_total_bytes": 1_048_576,
                         "max_response_bytes": 262_144, "timeout_seconds": 40,
                         "forecast_only": True, "on_failure": "stop; preserve partial files; no substitution"},
        "prior_budget_review": {"p0_recorded_estimate": 2299.5,
                                "p0_conservative_reestimate": float(p0_weight),
                                "prior_all_own_weather_allowance_rounded_up": prior,
                                "billing_status": "planning estimate; provider usage not observable"},
        "quota": {"daily_internal_cap": DAY_CAP, "hour_internal_cap": HOUR_CAP,
                  "minute_internal_cap": MINUTE_CAP, "minimum_start_interval_seconds": 4,
                  "other_shared_traffic_assumption_per_day": SHARED_RESERVE,
                  "automatic_retries": 0,
                  "execution_gate": "persist actual attempts; enforce rolling minute/hour/day limits; account for other traffic"},
        "daily_allotments_if_reuse_verified": reserve_batches(len(requests), prior=prior),
        "daily_allotments_without_reuse": reserve_batches(len(days), prior=prior),
        "requests": requests,
    }
