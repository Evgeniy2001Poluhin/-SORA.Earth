"""Synthetic JSON fixtures only. No public API or old private archives."""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError

import pytest

from app.services.forecasting import replay_p0 as replay


@pytest.fixture
def protocol():
    return json.loads((Path(__file__).parents[1] / replay.PROTOCOL).read_text())


@pytest.fixture
def small(protocol):
    protocol["points"] = [protocol["points"][0]]
    protocol["training_start"] = "2024-12-31"
    protocol["verification_end"] = "2025-01-07"
    protocol["origins"] = ["2024-12-31T23:59:59+00:00"]
    return protocol


def payload(protocol, item):
    start, end = replay._bounds(protocol, item)
    results = []
    for point in protocol["points"]:
        times, values = [], []
        for offset in range(((end-start).days+1)*24):
            moment = datetime.combine(start, datetime.min.time()) + timedelta(hours=offset)
            times.append(moment.strftime("%Y-%m-%dT%H:%M"))
            values.append((moment.date().toordinal() % 43) + moment.hour / 100.0)
        results.append({"latitude": point["lat"], "longitude": point["lon"],
                        "timezone": "GMT", "utc_offset_seconds": 0,
                        "hourly_units": {"time": "iso8601", "temperature_2m": "°C"},
                        "hourly": {"time": times, "temperature_2m": values}})
    return results


def raw_snapshot(tmp_path, protocol):
    def fake_fetch(url, timeout, limit):
        requests, _ = replay.request_plan(protocol)
        item = next(item for item in requests if item["url"] == url)
        return replay.canonical(payload(protocol, item))
    provenance = {"protocol_sha256": replay.digest(replay.canonical(protocol)),
                  "protocol_commit": "a"*40, "code_commit": "b"*40}
    directory = tmp_path / "snapshot"
    manifest = replay.acquire_snapshot(directory, protocol, provenance, fetch=fake_fetch)
    pin = replay.digest(replay.canonical(manifest))
    return directory, provenance, pin


def test_frozen_schedule_covers_all_points_and_calendar_seasons(protocol):
    requests, weighted = replay.request_plan(protocol)
    assert len(protocol["points"]) == 21
    assert len(requests) == 13
    assert 0 < weighted < protocol["acquisition"]["max_estimated_weighted_calls"]
    assert [datetime.fromisoformat(o).month for o in protocol["origins"]] == [12,1,2,3,4,5,6,7,8,9,10,11]
    assert protocol["role"] == "exploratory_only"
    for item in requests[1:]:
        origin = datetime.fromisoformat(item["origin"])
        assert origin.hour == 23 and origin.minute == 59
        assert (origin.date()+timedelta(days=1)).day == 1


def test_positive_grid_and_exact_daily_means(small):
    item = replay.request_plan(small)[0][0]
    data = replay.parse_payload(replay.canonical(payload(small, item)), small, item)
    expected = date(2024,12,31).toordinal()%43 + sum(range(24))/24/100
    assert data["DEU"][date(2024,12,31)] == pytest.approx(expected)


@pytest.mark.parametrize("missing,accepted", [(0,True),(5,True),(6,False),(24,False)])
def test_verification_nulls_are_missing_and_floor_is_distinct_hours(small, missing, accepted):
    item = replay.request_plan(small)[0][0]
    data = payload(small, item)
    data[0]["hourly"]["temperature_2m"][:missing] = [None]*missing
    if accepted:
        assert replay.parse_payload(replay.canonical(data),small,item)
    else:
        with pytest.raises(replay.ReplayRefusal,match="required distinct hours"):
            replay.parse_payload(replay.canonical(data),small,item)


def test_forecast_requires_every_hour(small):
    item = replay.request_plan(small)[0][1]
    data = payload(small, item);data[0]["hourly"]["temperature_2m"][24] = None
    with pytest.raises(replay.ReplayRefusal,match="required distinct hours"):
        replay.parse_payload(replay.canonical(data),small,item)


def test_unused_origin_day_is_not_a_hidden_coverage_requirement(small):
    item = replay.request_plan(small)[0][1]
    data = payload(small, item)
    data[0]["hourly"]["temperature_2m"][:24] = [None]*24
    assert len(replay.parse_payload(replay.canonical(data),small,item)["DEU"]) == 7


@pytest.mark.parametrize("mutation", ["duplicate","absent_day","missing_point","arrays","offset","half_hour",
                                     "unit","zone","zone_bool","location","bool","infinity"])
def test_bad_public_payloads_refuse_without_filling(small, mutation):
    item = replay.request_plan(small)[0][0];data = payload(small,item);point=data[0]
    if mutation == "duplicate": point["hourly"]["time"][1] = point["hourly"]["time"][0]
    elif mutation == "absent_day":
        for key in point["hourly"]:point["hourly"][key] = point["hourly"][key][24:]
    elif mutation == "missing_point":data=[]
    elif mutation == "arrays":point["hourly"]["time"].pop()
    elif mutation == "offset":point["hourly"]["time"][0] += "+03:00"
    elif mutation == "half_hour":point["hourly"]["time"][0] = "2024-12-31T00:30"
    elif mutation == "unit":point["hourly_units"]["temperature_2m"] = "°F"
    elif mutation == "zone":point["timezone"] = "Europe/Berlin"
    elif mutation == "zone_bool":point["utc_offset_seconds"] = False
    elif mutation == "location":point["latitude"] += 1
    elif mutation == "bool":point["hourly"]["temperature_2m"][0] = True
    else:point["hourly"]["temperature_2m"][0] = float("inf")
    raw=json.dumps(data,ensure_ascii=False).encode()
    with pytest.raises(replay.ReplayRefusal):replay.parse_payload(raw,small,item)


def test_duplicate_json_keys_are_not_last_one_wins():
    with pytest.raises(replay.ReplayRefusal):replay.parse_json(b'{"timezone":"GMT","timezone":"UTC"}')


def test_reordered_points_are_rejected(protocol):
    protocol["points"] = protocol["points"][:2]
    protocol["training_start"] = protocol["verification_end"] = "2024-01-01"
    item=replay.request_plan(protocol)[0][0];data=payload(protocol,item);data.reverse()
    with pytest.raises(replay.ReplayRefusal,match="location"):
        replay.parse_payload(replay.canonical(data),protocol,item)


def test_bytes_read_are_the_bytes_hashed(tmp_path,small):
    directory,_,pin=raw_snapshot(tmp_path,small)
    manifest,actual_pin,parsed=replay.read_snapshot(directory,small,pin)
    assert actual_pin==pin and len(parsed)==2 and manifest["complete"]
    path=directory/"verification.json";path.write_bytes(path.read_bytes()+b" ")
    with pytest.raises(replay.ReplayRefusal,match="pin mismatch"):
        replay.read_snapshot(directory,small,pin)


def test_external_manifest_pin_cannot_be_recomputed_by_the_snapshot(tmp_path,small):
    directory,_,pin=raw_snapshot(tmp_path,small)
    path=directory/"manifest.json";path.write_bytes(path.read_bytes()+b" ")
    with pytest.raises(replay.ReplayRefusal,match="external manifest pin"):
        replay.read_snapshot(directory,small,pin)


def test_no_existing_output_directory_is_modified(tmp_path,small):
    directory=tmp_path/"existing";directory.mkdir();marker=directory/"keep";marker.write_text("user")
    with pytest.raises(replay.ReplayRefusal,match="new directory"):
        replay.acquire_snapshot(directory,small,{},fetch=lambda *a:pytest.fail("network"))
    assert marker.read_text()=="user"


def test_http_failure_keeps_partial_without_complete_manifest(tmp_path,small):
    directory=tmp_path/"incomplete"
    def failure(*args):raise HTTPError("public",400,"bad",None,None)
    with pytest.raises(replay.ReplayRefusal,match="HTTP 400"):
        replay.acquire_snapshot(directory,small,{},fetch=failure)
    assert directory.exists() and not (directory/"manifest.json").exists()


def test_fetch_refuses_private_or_redirectable_endpoints_before_network():
    with pytest.raises(replay.ReplayRefusal,match="unapproved"):
        replay._fetch("http://localhost/private",1,100)


def test_full_synthetic_evaluation_keeps_every_method_and_uses_past_prefix(tmp_path,small,monkeypatch):
    small["training_start"]="2022-01-01"
    directory,provenance,pin=raw_snapshot(tmp_path,small)
    original=replay.evaluate_rolling_origin;seen=[]
    def checked(fit_predict,frame,target,horizon,train_size,*args,**kwargs):
        def guarded(train,h):
            assert len(train)==train_size
            assert train["ds"].max().date()==date(2024,12,31)
            assert train["ds"].max()<frame.iloc[train_size]["ds"]
            seen.append(len(train))
            return fit_predict(train,h)
        return original(guarded,frame,target,horizon,train_size,*args,**kwargs)
    monkeypatch.setattr(replay,"evaluate_rolling_origin",checked)
    report=replay.evaluate_snapshot(directory,small,provenance,pin)
    assert len(seen)==5 and report["points"]==1 and report["windows_per_point"]==1
    assert set(report["methods"])==set(small["methods"])
    assert not report["promotion_allowed"]
    assert report["verdict"]=="exploratory_only_no_confirmatory_claim"
    for result in report["methods"].values():
        assert set(result["by_point"])=={"DEU"}
        assert len(result["windows"]["DEU"])==1


def test_full_evaluation_refuses_changed_protocol_pin_before_fitting(tmp_path,small,monkeypatch):
    directory,provenance,pin=raw_snapshot(tmp_path,small)
    provenance={**provenance,"protocol_sha256":"wrong"}
    monkeypatch.setattr(replay,"default_baselines",lambda:pytest.fail("fit must not start"))
    with pytest.raises(replay.ReplayRefusal,match="protocol pin"):
        replay.evaluate_snapshot(directory,small,provenance,pin)
