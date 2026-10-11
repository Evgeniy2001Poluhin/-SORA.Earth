"""Load and validate the machine-readable contracts for registered sources.

The Python source register remains authoritative for scheduling and source
classification.  These JSON files add the data-facing contract: access,
rights, temporal meaning, identity, missingness and normalized fields.  Loading
is strict so an incomplete or extra contract cannot quietly enter a release.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from app.ingesters.source_register import SOURCE_REGISTER


CONTRACT_ROOT = Path(__file__).resolve().parents[1] / "data_contracts"
SCHEMA_PATH = CONTRACT_ROOT / "source-contract.schema.json"
SOURCES_PATH = CONTRACT_ROOT / "sources"


class SourceContractError(ValueError):
    """A committed source contract is absent, invalid, or contradicts code."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceContractError(f"cannot read source contract {path.name}: {exc}") from exc


def load_source_contracts() -> dict[str, dict[str, Any]]:
    """Return all contracts after schema and source-register consistency checks."""
    schema = _read_json(SCHEMA_PATH)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    contracts: dict[str, dict[str, Any]] = {}

    for path in sorted(SOURCES_PATH.glob("*.json")):
        contract = _read_json(path)
        errors = sorted(validator.iter_errors(contract), key=lambda error: list(error.path))
        if errors:
            details = "; ".join(
                f"{'/'.join(map(str, error.path)) or '<root>'}: {error.message}"
                for error in errors
            )
            raise SourceContractError(f"invalid source contract {path.name}: {details}")

        source_id = contract["source_id"]
        if path.stem != source_id:
            raise SourceContractError(
                f"contract filename {path.name!r} does not match source_id {source_id!r}"
            )
        if source_id in contracts:
            raise SourceContractError(f"duplicate source contract: {source_id}")
        contracts[source_id] = contract

    expected = set(SOURCE_REGISTER)
    actual = set(contracts)
    if actual != expected:
        raise SourceContractError(
            f"source contract set differs from SOURCE_REGISTER; "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )

    for source_id, facts in SOURCE_REGISTER.items():
        contract = contracts[source_id]
        for field, expected_value in (
            ("status", facts.status),
            ("measurement_kind", facts.measurement_kind),
            ("coverage", facts.coverage),
        ):
            if contract[field] != expected_value:
                raise SourceContractError(
                    f"{source_id}.{field}={contract[field]!r} contradicts "
                    f"SOURCE_REGISTER value {expected_value!r}"
                )

    return contracts


def source_contract(source_id: str) -> dict[str, Any]:
    """Return one validated contract, refusing an unknown source id."""
    try:
        return load_source_contracts()[source_id]
    except KeyError as exc:
        raise SourceContractError(f"unknown source contract: {source_id}") from exc


def validate_normalized_field(
    source_id: str, metric: str, value: Any, unit: str | None
) -> None:
    """Validate one normalized value without mutating or coercing it.

    Parsers remain responsible for transforming publisher payloads.  This gate
    checks the result of that transformation and refuses unknown fields, unit
    drift, non-finite numbers, nullability violations, and declared bounds.
    """
    contract = source_contract(source_id)
    fields = {field["name"]: field for field in contract["fields"]}
    try:
        field = fields[metric]
    except KeyError as exc:
        raise SourceContractError(
            f"{source_id} emitted field {metric!r} outside contract v{contract['contract_version']}"
        ) from exc

    if value is None:
        if field["nullable"]:
            return
        raise SourceContractError(f"{source_id}.{metric} is not nullable")

    expected_type = field["type"]
    if isinstance(value, bool):
        raise SourceContractError(f"{source_id}.{metric} boolean is not {expected_type}")
    if expected_type == "number":
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise SourceContractError(f"{source_id}.{metric} must be a finite number")
    elif expected_type == "integer":
        if not isinstance(value, int):
            raise SourceContractError(f"{source_id}.{metric} must be an integer")
    elif not isinstance(value, str):
        raise SourceContractError(f"{source_id}.{metric} must be a string")

    if unit != field["unit"]:
        raise SourceContractError(
            f"{source_id}.{metric} unit {unit!r} does not match {field['unit']!r}"
        )
    if "minimum" in field and value < field["minimum"]:
        raise SourceContractError(
            f"{source_id}.{metric} value {value!r} is below {field['minimum']!r}"
        )
    if "maximum" in field and value > field["maximum"]:
        raise SourceContractError(
            f"{source_id}.{metric} value {value!r} is above {field['maximum']!r}"
        )
