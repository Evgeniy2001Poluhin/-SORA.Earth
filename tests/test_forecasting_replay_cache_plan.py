"""Calendar-only tests: no HTTP, forecast values or labels."""
from datetime import date, timedelta
from fractions import Fraction
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app.services.forecasting import replay_cache_plan as cache
from app.services.forecasting import replay_calibration as calibration


@pytest.mark.parametrize("days,expected", [(1,21),(8,21),(14,21),(15,Fraction(45,2)),(28,42)])
def test_weight_reserves_at_least_one_unit_per_location(days, expected):
    assert cache.request_weight(locations=21, days=days) == expected


@pytest.mark.parametrize("locations,days", [(True,8),(21,0),(-1,8),(21,8.0)])
def test_budget_rejects_invalid_units(locations, days):
    with pytest.raises(ValueError): cache.request_weight(locations=locations,days=days)


def test_calendar_matches_frozen_p0_and_does_not_change_population():
    frozen=json.loads((Path(__file__).parents[1]/"docs/research/M3_REPLAY_P0.json").read_text())
    assert [o.isoformat() for o in cache.origins()] == frozen["origins"]
    assert cache.POINT_COUNT == len(frozen["points"]) == 21
    assert (cache.WINDOW,cache.MIN_PAIRS,cache.HORIZON)==(calibration.WINDOW_DAYS,calibration.MIN_PAIRS,7)


def test_plan_covers_whole_daily_grid_without_duplicate_or_scoring_claim():
    plan=cache.build_plan()
    new={r["issue_date"] for r in plan["requests"]}
    old={r["issue_date"] for r in plan["reuse_candidates"]}
    first=date.fromisoformat(plan["start_issue_date"]);last=date.fromisoformat(plan["end_issue_date"])
    expected={(first+timedelta(days=i)).isoformat() for i in range((last-first).days+1)}
    assert new.isdisjoint(old)
    assert len(new)==len(plan["requests"])
    assert new|old==expected
    assert len(new)==596 and len(old)==11 and len(expected)==607
    assert "not downloaded" in plan["coverage_status"]
    assert "no real calibration" in plan["role"]


def test_stage_one_has_minimum_mature_pairs_for_every_lead():
    plan=cache.build_plan()
    origin=cache.origins()[0]
    first=[date.fromisoformat(r["issue_date"]) for r in plan["requests"][:180]]
    assert first[0]==date(2024,6,27) and first[-1]==date(2024,12,23)
    for lead in range(1,8):
        rows=[calibration.CalibrationPair("DEU",cache.cutoff(d),lead,10,12) for d in first]
        result=calibration.fit_correction(rows,point_code="DEU",lead_day=lead,origin=origin)
        assert result.eligible_pairs==180 and result.status=="calibrated"
    assert plan["stage_one"]["reserved_weight"]==3780


def test_full_potential_coverage_minimum_and_maturity_boundaries():
    plan=cache.build_plan()
    rows=plan["potential_counts_same_for_each_declared_point"]
    assert list(rows[0]["per_point_eligible_by_lead"].values())==[273,272,271,270,269,268,267]
    assert list(rows[-1]["per_point_eligible_by_lead"].values())==[364,363,362,361,360,359,358]
    assert min(v for r in rows for v in r["per_point_eligible_by_lead"].values())>=180
    origin=cache.origins()[0]
    assert cache.eligible(origin.date()-timedelta(days=8),origin,7)
    assert not cache.eligible(origin.date()-timedelta(days=7),origin,7)
    assert cache.eligible(origin.date()-timedelta(days=365),origin,1)
    assert not cache.eligible(origin.date()-timedelta(days=366),origin,1)


def test_request_and_probe_contracts_are_forecast_only_and_bounded():
    plan=cache.build_plan()
    assert len(plan["probe_requests"])==plan["probe_policy"]["max_http_attempts"]==3
    assert plan["probe_policy"]["retries"]==0
    assert [r["issue_date"] for r in plan["probe_requests"]]==["2024-06-27","2024-09-24","2024-12-23"]
    for r in plan["requests"]:
        url=urlsplit(r["url"]);q=parse_qs(url.query)
        assert url.scheme=="https" and url.netloc=="single-runs-api.open-meteo.com"
        assert q["models"]==["ecmwf_ifs"] and q["forecast_days"]==["8"]
        assert q["run"]==[r["issue_date"]+"T00:00"]
        assert len(q["latitude"][0].split(","))==21
        assert q["hourly"]==["temperature_2m"] and q["timezone"]==["UTC"]


def test_budget_reestimate_and_allotments_do_not_underreserve():
    plan=cache.build_plan();budget=plan["prior_budget_review"]
    assert budget["p0_conservative_reestimate"]==2407.5
    assert budget["prior_all_own_weather_allowance_rounded_up"]==2409
    assert plan["new_weight_if_reuse_verified"]==12516
    assert plan["new_weight_without_reuse"]==12747
    batches=plan["daily_allotments_if_reuse_verified"]
    assert [b["http_attempts"] for b in batches]==[218,333,45]
    for b in batches:
        assert b["reserved_weight"]+b["prior_weight_allowance"]+b["other_traffic_allowance"]<=cache.DAY_CAP
        assert sum(b["hour_allotments"])==b["http_attempts"]
        assert all(n*21<=cache.HOUR_CAP for n in b["hour_allotments"])
    assert 21*60/plan["quota"]["minimum_start_interval_seconds"]<cache.MINUTE_CAP


def test_existing_usage_can_force_zero_requests_today():
    batches=cache.reserve_batches(1,prior=cache.DAY_CAP)
    assert batches[0]["http_attempts"]==0 and batches[1]["http_attempts"]==1
    assert cache.reserve_batches(0,prior=0)==[]


@pytest.mark.parametrize("kw", [{"count":True,"prior":0},{"count":1,"prior":-1},
                                {"count":1,"prior":0,"reserve":cache.DAY_CAP}])
def test_invalid_batch_assumptions_refuse(kw):
    with pytest.raises(ValueError): cache.reserve_batches(**kw)
