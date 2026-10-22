"""Synthetic metadata only: no archive, observed outcomes, model fit or network."""
from __future__ import annotations

import copy
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services.forecasting.replay_readiness import (
    ReplayPlanError,
    check_replay_plan,
    expected_hours,
)


@pytest.fixture
def plan():
    origin = datetime(2024, 6, 1, tzinfo=timezone.utc)
    hours = sorted(expected_hours(origin, 7))
    return {
        "schema_version": 1,
        "study_role": "exploratory",
        "outcome_exposure": "seen",
        "source_kind": "operational_archive",
        "availability_basis": "contemporaneous_receipt",
        "forecast_snapshot_sha256": "a" * 64,
        "verification_snapshot_sha256": "b" * 64,
        "availability_evidence_sha256": "c" * 64,
        "exposure_log_sha256": "d" * 64,
        "provider": "synthetic-provider",
        "model": "synthetic-model",
        "model_version": "frozen-v1",
        "verification_target": "synthetic daily verification, not thermometers",
        "target": "temperature_2m_daily_mean_utc",
        "temperature_unit": "degC",
        "horizon_days": 7,
        "points": ["TEST"],
        "origins": [origin.isoformat()],
        "runs": [{
            "point": "TEST",
            "origin": origin.isoformat(),
            "initialised_at": (origin - timedelta(hours=24)).isoformat(),
            "available_at": (origin - timedelta(hours=18)).isoformat(),
            "retrieved_at": "2026-10-22T00:00:00Z",
            "model_version": "frozen-v1",
            "payload_sha256": "e" * 64,
            "availability_receipt_sha256": "f" * 64,
            "valid_hours": [hour.isoformat() for hour in hours],
        }],
    }


def test_complete_metadata_is_not_skill_or_live_eligibility(plan):
    report = check_replay_plan(plan)
    assert report["forecast_hours"] == 168
    assert report["windows"] == 1
    assert report["points"] == 1
    assert report["status"] == "metadata_consistent_requires_independent_review"
    for name in ("authenticity_verified", "outcome_coverage_verified",
                 "skill_evaluated", "promotion_allowed"):
        assert report[name] is False
    assert "not live M3" in report["result_label"]


def test_future_download_of_an_old_original_run_is_allowed(plan):
    # This is archived-provider replay, never the system's strict fetched_at mode.
    assert check_replay_plan(plan)["study_role"] == "exploratory"


def test_one_origin_uses_a_complete_horizon_not_only_the_last_lead():
    origin = datetime(2024, 6, 1, 18, tzinfo=timezone.utc)
    hours = expected_hours(origin, 7)
    assert len(hours) == 168
    assert min(hours) == datetime(2024, 6, 2, tzinfo=timezone.utc)
    assert max(hours) == datetime(2024, 6, 8, 23, tzinfo=timezone.utc)


@pytest.mark.parametrize("kind", ["hindcast", "reforecast", "stitched", "reanalysis", None])
def test_nonoperational_sources_are_not_silently_upgraded(plan, kind):
    plan["source_kind"] = kind
    with pytest.raises(ReplayPlanError, match="source_kind"):
        check_replay_plan(plan)


@pytest.mark.parametrize("basis", ["assumed_lag", "model_initialisation", "unknown", None])
def test_initialisation_plus_assumed_lag_is_not_receipt_evidence(plan, basis):
    plan["availability_basis"] = basis
    with pytest.raises(ReplayPlanError, match="availability_basis"):
        check_replay_plan(plan)


@pytest.mark.parametrize("exposure", ["seen", "unknown"])
def test_old_or_unknown_outcomes_cannot_be_new_confirmation(plan, exposure):
    plan.update(study_role="confirmatory", outcome_exposure=exposure)
    with pytest.raises(ReplayPlanError, match="fresh holdout"):
        check_replay_plan(plan)


def test_unseen_confirmation_still_needs_independent_review(plan):
    plan.update(study_role="confirmatory", outcome_exposure="unseen")
    assert not check_replay_plan(plan)["authenticity_verified"]


@pytest.mark.parametrize("field", ["available_at", "initialised_at"])
def test_run_not_available_at_origin_is_rejected(plan, field):
    plan["runs"][0][field] = "2024-06-01T06:00:00Z"
    with pytest.raises(ReplayPlanError, match="not available|after forecast origin"):
        check_replay_plan(plan)


def test_reconstruction_can_be_studied_now_without_backdating_availability(plan):
    plan.update(source_kind="retrospective_reconstruction",
                availability_basis="retrieved_vintage_only")
    plan["runs"][0]["available_at"] = plan["runs"][0]["retrieved_at"]
    report = check_replay_plan(plan)
    assert "pseudo-real-time" in report["result_label"]
    assert "not a real-time claim" in report["result_label"]
    assert report["promotion_allowed"] is False


def test_reconstruction_cannot_claim_fresh_confirmation_even_if_declared_unseen(plan):
    plan.update(source_kind="retrospective_reconstruction",
                availability_basis="retrieved_vintage_only",
                study_role="confirmatory", outcome_exposure="unseen")
    with pytest.raises(ReplayPlanError, match="only supports an exploratory"):
        check_replay_plan(plan)


def test_reconstruction_must_not_inherit_original_availability_label(plan):
    plan["source_kind"] = "retrospective_reconstruction"
    with pytest.raises(ReplayPlanError, match="availability_basis"):
        check_replay_plan(plan)


def test_retrieval_cannot_precede_publication(plan):
    plan["runs"][0]["retrieved_at"] = "2024-05-30T00:00:00Z"
    with pytest.raises(ReplayPlanError, match="retrieval precedes"):
        check_replay_plan(plan)


@pytest.mark.parametrize("timestamp", ["2024-05-31T00:00:00", "2024-05-31T03:00:00+03:00", "bad", None])
def test_timezone_is_explicit_utc_not_relabelled(plan, timestamp):
    plan["runs"][0]["initialised_at"] = timestamp
    with pytest.raises(ReplayPlanError, match="timestamp|UTC"):
        check_replay_plan(plan)


@pytest.mark.parametrize("key", ["forecast_snapshot_sha256", "verification_snapshot_sha256",
                                 "availability_evidence_sha256", "exposure_log_sha256"])
def test_pins_cannot_be_missing_or_informal(plan, key):
    plan[key] = "not-pinned"
    with pytest.raises(ReplayPlanError, match="SHA-256"):
        check_replay_plan(plan)


@pytest.mark.parametrize("key", ["payload_sha256", "availability_receipt_sha256"])
def test_per_run_byte_and_receipt_pins_are_required(plan, key):
    del plan["runs"][0][key]
    with pytest.raises(ReplayPlanError, match="SHA-256"):
        check_replay_plan(plan)


def test_seven_day_source_cannot_cover_thirty_day_request(plan):
    plan["horizon_days"] = 30
    with pytest.raises(ReplayPlanError, match="whole horizon"):
        check_replay_plan(plan)


def test_real_thirty_day_grid_is_supported(plan):
    plan["horizon_days"] = 30
    origin = datetime.fromisoformat(plan["origins"][0])
    plan["runs"][0]["valid_hours"] = [
        h.isoformat() for h in sorted(expected_hours(origin, 30))
    ]
    assert check_replay_plan(plan)["forecast_hours"] == 720


def test_timestamp_overflow_is_a_refusal_not_a_crash(plan):
    plan["origins"] = ["9999-12-31T00:00:00Z"]
    with pytest.raises(ReplayPlanError, match="timestamp range"):
        check_replay_plan(plan)


def test_declared_grid_size_is_bounded_before_cross_product(plan):
    plan["points"] = [str(i) for i in range(22)]
    with pytest.raises(ReplayPlanError, match="at most 21"):
        check_replay_plan(plan)


def test_origin_count_is_bounded_before_materialising_hour_grids(plan):
    first = datetime(2000, 1, 1, tzinfo=timezone.utc)
    plan["origins"] = [(first + timedelta(days=7*i)).isoformat() for i in range(521)]
    with pytest.raises(ReplayPlanError, match="at most 520"):
        check_replay_plan(plan)


@pytest.mark.parametrize("change", ["missing", "duplicate", "extra", "offset", "only_day7"])
def test_full_hour_grid_has_no_holes_or_pseudoreplication(plan, change):
    hours = plan["runs"][0]["valid_hours"]
    if change == "missing":
        hours.pop(70)
    elif change == "duplicate":
        hours.append(hours[0])
    elif change == "extra":
        hours.append("2024-06-09T00:00:00Z")
    elif change == "offset":
        hours[70] = hours[70].replace(":00:00+00:00", ":30:00+00:00")
    else:
        plan["runs"][0]["valid_hours"] = hours[-24:]
    with pytest.raises(ReplayPlanError):
        check_replay_plan(plan)


def test_grid_rejects_duplicate_instants_with_different_spelling(plan):
    hours = plan["runs"][0]["valid_hours"]
    hours.append(hours[0].replace("+00:00", "Z"))
    with pytest.raises(ReplayPlanError, match="duplicate instant"):
        check_replay_plan(plan)


@pytest.mark.parametrize("field", ["points", "origins", "runs"])
def test_empty_design_is_not_ready(plan, field):
    plan[field] = []
    with pytest.raises(ReplayPlanError, match="nonempty"):
        check_replay_plan(plan)


def test_missing_point_cannot_shrink_denominator(plan):
    plan["points"].append("MISSING")
    with pytest.raises(ReplayPlanError, match="missing declared"):
        check_replay_plan(plan)


@pytest.mark.parametrize("field,value", [("point", "UNDECLARED"),
                                        ("origin", "2024-06-08T00:00:00Z"),
                                        ("model_version", "other-vintage")])
def test_misaligned_run_is_refused(plan, field, value):
    plan["runs"][0][field] = value
    with pytest.raises(ReplayPlanError, match="undeclared|mixed model"):
        check_replay_plan(plan)


def test_two_runs_cannot_be_stitched_at_one_origin(plan):
    plan["runs"].append(copy.deepcopy(plan["runs"][0]))
    with pytest.raises(ReplayPlanError, match="duplicate point/origin"):
        check_replay_plan(plan)


def test_adjacent_windows_do_not_overlap(plan):
    second = copy.deepcopy(plan["runs"][0])
    origin = datetime(2024, 6, 8, tzinfo=timezone.utc)
    second["origin"] = origin.isoformat()
    second["valid_hours"] = [h.isoformat() for h in sorted(expected_hours(origin, 7))]
    plan["origins"].append(origin.isoformat())
    plan["runs"].append(second)
    assert check_replay_plan(plan)["windows"] == 2


def test_overlapping_windows_are_not_independent_folds(plan):
    plan["origins"].append("2024-06-07T00:00:00Z")
    with pytest.raises(ReplayPlanError, match="overlap"):
        check_replay_plan(plan)


@pytest.mark.parametrize("field,value", [("schema_version", True), ("horizon_days", True),
                                        ("horizon_days", 16), ("temperature_unit", "F"),
                                        ("target", "monthly_anomaly"), ("model", "x\ny")])
def test_unsupported_contract_is_rejected(plan, field, value):
    plan[field] = value
    with pytest.raises(ReplayPlanError):
        check_replay_plan(plan)


@pytest.fixture
def cli():
    path = Path(__file__).resolve().parents[1] / "scripts/check_forecast_replay_readiness.py"
    spec = importlib.util.spec_from_file_location("replay_readiness_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_accepts_metadata_but_denies_skill_claim(plan, cli, tmp_path, capsys):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    assert cli.main([str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["skill_evaluated"] is False


def test_cli_refusal_has_nonzero_exit(plan, cli, tmp_path, capsys):
    plan["availability_basis"] = "assumed_lag"
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    assert cli.main([str(path)]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "refused"


@pytest.mark.parametrize("raw", ['{"schema_version":1,"schema_version":1}', '{', '[]'])
def test_cli_rejects_ambiguous_or_malformed_json(raw, cli, tmp_path, capsys):
    path = tmp_path / "plan.json"
    path.write_text(raw)
    assert cli.main([str(path)]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "refused"


def test_cli_bounds_input(cli, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "MAX_BYTES", 8)
    path = tmp_path / "large.json"
    path.write_bytes(b" " * 9)
    assert cli.main([str(path)]) == 2
    assert "exceeds" in capsys.readouterr().out


def test_cli_os_error_does_not_print_private_filename(cli, tmp_path, capsys):
    path = tmp_path / "private-name.json"
    assert cli.main([str(path)]) == 2
    output = capsys.readouterr().out
    assert "private-name" not in output
    assert "FileNotFoundError" in output
