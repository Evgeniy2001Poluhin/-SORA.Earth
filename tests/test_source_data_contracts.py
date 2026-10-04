import copy
import json

import pytest

from app.ingesters import data_contracts
from app.ingesters.source_register import SOURCE_REGISTER


def test_every_registered_source_has_one_valid_contract():
    contracts = data_contracts.load_source_contracts()

    assert set(contracts) == set(SOURCE_REGISTER)


def test_contracts_preserve_registered_classification_and_coverage():
    contracts = data_contracts.load_source_contracts()

    for source_id, facts in SOURCE_REGISTER.items():
        contract = contracts[source_id]
        assert contract["status"] == facts.status
        assert contract["measurement_kind"] == facts.measurement_kind
        assert contract["coverage"] == facts.coverage


def test_unverified_rights_never_carry_a_license_claim():
    contracts = data_contracts.load_source_contracts()

    for contract in contracts.values():
        rights = contract["rights"]
        if rights["verification_status"] == "unverified":
            assert rights["license_id"] is None
            assert rights["terms_url"] is None
            assert rights["attribution_required"] is None
            assert rights["verified_on"] is None


def test_verified_rights_include_a_dated_source_and_attribution_decision():
    contracts = data_contracts.load_source_contracts()

    verified = [c for c in contracts.values() if c["rights"]["verification_status"] == "verified"]
    assert verified
    for contract in verified:
        rights = contract["rights"]
        assert rights["license_id"]
        assert rights["terms_url"].startswith("https://")
        assert isinstance(rights["attribution_required"], bool)
        assert rights["verified_on"]


def test_field_names_are_unique_inside_each_contract():
    for source_id, contract in data_contracts.load_source_contracts().items():
        names = [field["name"] for field in contract["fields"]]
        assert len(names) == len(set(names)), source_id


def test_ingester_contract_fields_match_the_real_normalized_outputs():
    from app.ingesters.openaq import PARAMETER_RANGES
    from app.ingesters.openmeteo import OpenMeteoIngester, WEATHER_VARIABLES
    from app.ingesters.openmeteo_air_quality import POLLUTANTS

    contracts = data_contracts.load_source_contracts()
    openaq = {field["name"]: field["unit"] for field in contracts["openaq"]["fields"]}
    assert openaq == {value["metric"]: "ug/m3" for value in PARAMETER_RANGES.values()}

    openmeteo = {field["name"]: field["unit"] for field in contracts["openmeteo"]["fields"]}
    assert openmeteo == dict(OpenMeteoIngester._map_variable(name) for name in WEATHER_VARIABLES)

    air_quality = {
        field["name"]: field["unit"]
        for field in contracts["openmeteo_air_quality"]["fields"]
    }
    assert air_quality == POLLUTANTS


def test_country_contract_fields_follow_the_actual_indicator_sets():
    from app.country_benchmarks import GLOBAL_AVG
    from app.external_data import INDICATORS, OECD_FLOWS

    contracts = data_contracts.load_source_contracts()
    assert {field["name"] for field in contracts["world_bank"]["fields"]} == set(INDICATORS)
    assert {field["name"] for field in contracts["oecd"]["fields"]} == set(OECD_FLOWS)
    assert {field["name"] for field in contracts["benchmark"]["fields"]} == set(GLOBAL_AVG)
    assert {field["name"] for field in contracts["global_avg"]["fields"]} == set(GLOBAL_AVG)


def test_contract_files_are_canonical_json():
    for path in sorted(data_contracts.SOURCES_PATH.glob("*.json")):
        parsed = json.loads(path.read_text(encoding="utf-8"))
        expected = json.dumps(parsed, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        assert path.read_text(encoding="utf-8") == expected


def test_a_contract_cannot_silently_contradict_the_source_register(monkeypatch, tmp_path):
    contracts = data_contracts.load_source_contracts()
    changed = copy.deepcopy(contracts["openmeteo"])
    changed["measurement_kind"] = "measured"

    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    for source_id, contract in contracts.items():
        value = changed if source_id == "openmeteo" else contract
        (source_dir / f"{source_id}.json").write_text(json.dumps(value), encoding="utf-8")

    monkeypatch.setattr(data_contracts, "SOURCES_PATH", source_dir)
    with pytest.raises(data_contracts.SourceContractError, match="contradicts"):
        data_contracts.load_source_contracts()


def test_unknown_source_is_refused():
    with pytest.raises(data_contracts.SourceContractError, match="unknown source"):
        data_contracts.source_contract("not_a_source")


@pytest.mark.parametrize(
    ("source_id", "metric", "value", "unit"),
    [
        ("openmeteo", "humidity", 0.0, "percent"),
        ("openmeteo", "humidity", 100.0, "percent"),
        ("openaq", "pm25_ugm3", 0.0, "ug/m3"),
        ("rosstat", "budget_transparency", 100.0, "0-100"),
        ("sber_veb_baseline", "esg_index_baseline", 48.0, "0-100"),
    ],
)
def test_normalized_boundary_values_pass_the_contract(source_id, metric, value, unit):
    data_contracts.validate_normalized_field(source_id, metric, value, unit)


@pytest.mark.parametrize(
    ("metric", "value", "unit", "message"),
    [
        ("humidity", None, "percent", "not nullable"),
        ("humidity", float("nan"), "percent", "finite number"),
        ("humidity", float("inf"), "percent", "finite number"),
        ("humidity", -1.0, "percent", "below"),
        ("humidity", 101.0, "percent", "above"),
        ("humidity", 50.0, "%", "unit"),
        ("not_a_metric", 1.0, None, "outside contract"),
    ],
)
def test_normalized_contract_violations_fail_closed(metric, value, unit, message):
    with pytest.raises(data_contracts.SourceContractError, match=message):
        data_contracts.validate_normalized_field("openmeteo", metric, value, unit)
