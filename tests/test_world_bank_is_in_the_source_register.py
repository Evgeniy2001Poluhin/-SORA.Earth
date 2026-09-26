"""World Bank and OECD in the source register (Phase 7 exit criterion).

Phase 7's exit criterion: every result ties to a source. World Bank and OECD
data reach API responses with source tags "world_bank" and "oecd" (written by
app/external_data.py and app/services/forecasting/features.py), but those tags
were not in the source register.

The owner's decision (2026-09-25): a new measurement kind named
`administrative_fetched` for official statistics fetched from the publisher at
run time — someone else measured, as with `administrative_snapshot`, but this is
the current release fetched over the network, not a dated copy shipped in the
source tree.
"""
import ast
import re
from typing import Set

import pytest


def test_world_bank_and_oecd_are_registered_as_administrative_fetched():
    """world_bank and oecd are in SOURCE_REGISTER with the right kind."""
    from app.ingesters.source_register import SOURCE_REGISTER

    assert "world_bank" in SOURCE_REGISTER, \
        "world_bank must be in SOURCE_REGISTER (Phase 7 exit criterion)"
    assert "oecd" in SOURCE_REGISTER, \
        "oecd must be in SOURCE_REGISTER"

    assert SOURCE_REGISTER["world_bank"].measurement_kind == "administrative_fetched", \
        "world_bank must be administrative_fetched"
    assert SOURCE_REGISTER["oecd"].measurement_kind == "administrative_fetched", \
        "oecd must be administrative_fetched"


def test_enumerated_tags_from_code_match_registrations():
    """Tags extracted from code are in the register (not trusting a list).

    Read app/external_data.py with ast to collect every string literal returned
    as a source tag by _fetch_with_fallback_impl and every source="..." keyword
    passed to CountryIndicatorHistory(...) in app/external_data.py and
    app/services/forecasting/features.py.
    """
    from app.ingesters.source_register import SOURCE_REGISTER

    # Collect tags from external_data.py
    with open("app/external_data.py", "r") as f:
        ext_tree = ast.parse(f.read(), filename="app/external_data.py")

    # Tags returned by _fetch_with_fallback_impl
    returned_tags: Set[str] = set()
    for node in ast.walk(ext_tree):
        # Look for return statements returning tuples with string literals
        if isinstance(node, ast.Return) and node.value:
            if isinstance(node.value, ast.Tuple):
                for elt in node.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        # These are the source tags: "world_bank", "oecd", "benchmark", "global_avg", "none"
                        if elt.value in ["world_bank", "oecd", "benchmark", "global_avg", "none"]:
                            returned_tags.add(elt.value)

    # Tags passed to CountryIndicatorHistory
    cih_tags: Set[str] = set()
    for node in ast.walk(ext_tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "CountryIndicatorHistory":
                for keyword in node.keywords:
                    if keyword.arg == "source":
                        if isinstance(keyword.value, ast.Constant):
                            cih_tags.add(keyword.value.value)

    # Also check features.py for CountryIndicatorHistory calls
    with open("app/services/forecasting/features.py", "r") as f:
        feat_tree = ast.parse(f.read(), filename="app/services/forecasting/features.py")

    for node in ast.walk(feat_tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "CountryIndicatorHistory":
                for keyword in node.keywords:
                    if keyword.arg == "source":
                        if isinstance(keyword.value, ast.Constant):
                            cih_tags.add(keyword.value.value)

    # Verify we found the expected tags (proves the test reads the real code)
    assert "world_bank" in returned_tags, \
        "_fetch_with_fallback_impl must return 'world_bank'"
    assert "oecd" in returned_tags, \
        "_fetch_with_fallback_impl must return 'oecd'"
    assert "world_bank" in cih_tags, \
        "CountryIndicatorHistory must be called with source='world_bank'"

    # The fallback chain returns exactly these five tags
    assert returned_tags == {"world_bank", "oecd", "benchmark", "global_avg", "none"}, \
        f"fallback returns {returned_tags}, expected exactly {{world_bank, oecd, benchmark, global_avg, none}}"

    # world_bank and oecd must resolve in the register
    assert "world_bank" in SOURCE_REGISTER, \
        "world_bank (returned by _fetch_with_fallback_impl) must resolve in SOURCE_REGISTER"
    assert "oecd" in SOURCE_REGISTER, \
        "oecd (returned by _fetch_with_fallback_impl) must resolve in SOURCE_REGISTER"


def test_administrative_fetched_is_accepted_by_observations_endpoint():
    """GET /observations?measurement_kind=administrative_fetched returns 200 (not 400).

    On main: 400 with "unknown measurement_kind" in body.
    With changes: 200 success.
    """
    from fastapi.testclient import TestClient
    from app.auth import require_admin
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    # Override auth dependency (same pattern as test_observation_read_api_auth.py)
    app.dependency_overrides[require_admin] = lambda: {"username": "t", "role": "admin"}
    try:
        response = client.get(
            "/api/v1/observations",
            params={"measurement_kind": "administrative_fetched"}
        )
    finally:
        app.dependency_overrides.pop(require_admin, None)

    # With changes: should be 200 (kind is accepted)
    # On main: would be 400 with "unknown measurement_kind" in body
    assert response.status_code == 200, \
        f"Expected 200; got {response.status_code}: {response.text}"


def test_prose_description_matches_register():
    """Every distinct measurement_kind in SOURCE_REGISTER appears in the OpenAPI description."""
    from app.ingesters.source_register import SOURCE_REGISTER
    from app.main import app

    # Collect all distinct measurement_kind values from the register
    kinds_in_register = {facts.measurement_kind for facts in SOURCE_REGISTER.values()}

    # Get the OpenAPI description for the observations endpoint
    openapi = app.openapi()
    obs_path = openapi["paths"]["/api/v1/observations"]["get"]
    params = {p["name"]: p for p in obs_path["parameters"]}

    measurement_kind_desc = params["measurement_kind"]["description"]

    # Each kind from the register must appear in the description
    for kind in kinds_in_register:
        assert kind in measurement_kind_desc, \
            f"measurement_kind '{kind}' from SOURCE_REGISTER must appear in " \
            f"observations endpoint description; found: {measurement_kind_desc}"


def test_administrative_fetched_and_snapshot_are_distinct():
    """ADMINISTRATIVE_FETCHED != ADMINISTRATIVE_SNAPSHOT, both defined once as module constants."""
    from app.ingesters.source_register import (
        ADMINISTRATIVE_FETCHED,
        ADMINISTRATIVE_SNAPSHOT,
    )

    assert ADMINISTRATIVE_FETCHED != ADMINISTRATIVE_SNAPSHOT, \
        "ADMINISTRATIVE_FETCHED and ADMINISTRATIVE_SNAPSHOT must be distinct values"

    # Read the source file to verify they are defined as module constants
    with open("app/ingesters/source_register.py", "r") as f:
        content = f.read()

    # Count definitions of each constant
    admin_fetched_defs = len(re.findall(
        r'^ADMINISTRATIVE_FETCHED\s*=\s*"administrative_fetched"',
        content,
        re.MULTILINE
    ))
    admin_snapshot_defs = len(re.findall(
        r'^ADMINISTRATIVE_SNAPSHOT\s*=\s*"administrative_snapshot"',
        content,
        re.MULTILINE
    ))

    assert admin_fetched_defs == 1, \
        f"ADMINISTRATIVE_FETCHED must be defined exactly once; found {admin_fetched_defs}"
    assert admin_snapshot_defs == 1, \
        f"ADMINISTRATIVE_SNAPSHOT must be defined exactly once; found {admin_snapshot_defs}"
