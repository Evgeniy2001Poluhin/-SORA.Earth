"""The M3 target is declared before accumulation counts, and stays declared.

M2 closed as a negative result because the ESG score is constant: 1.00 distinct
values per region. This declares a target that measurement shows does move, so
the §7 clock has something to run against.

What this file guards is not the wording. It is the four choices that must not
be made twice -- once now and once, differently, when results are visible. That
is what M2's §9 exists for, and a document nobody checks drifts exactly the way
the README badges did.

The point set is read from the ingester rather than restated, because a
declaration that names points the code no longer visits is worse than none.
"""
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECLARATION = os.path.join(REPO_ROOT, "docs", "M3_FORECAST_DECLARATION.md")


@pytest.fixture(scope="module")
def text():
    """Whitespace collapsed: the file is hard-wrapped and a phrase this looks
    for can fall across a line break."""
    return re.sub(r"\s+", " ", open(DECLARATION, encoding="utf-8").read())


def test_one_target_and_it_is_named(text):
    assert "`openmeteo:temperature`. One target." in text
    assert "One target, not a set" in text


def test_the_point_set_has_its_own_version(text):
    """Not `ru-regions-v1`: that is 85 subjects of the Russian Federation, a
    different population, and reusing the name is what §1.4 forbids."""
    assert "`openmeteo-points-v1`" in text
    assert "not** `ru-regions-v1`" in text


def test_the_declared_points_are_the_points_the_ingester_visits(text):
    """Read off REGION_CAPITALS, so the declaration cannot name a set the code
    has stopped collecting."""
    from app.ingesters.openmeteo import REGION_CAPITALS

    declared = set(re.findall(r"\b(?:[A-Z]{3}|RU-[A-Z]{3})\b",
                              text.split("```")[1]))
    visited = {code for code, _lat, _lon in REGION_CAPITALS}

    assert declared == visited, (
        f"declared but not visited: {sorted(declared - visited)}; "
        f"visited but not declared: {sorted(visited - declared)}"
    )


def test_the_target_is_not_described_as_regional(text):
    """Nineteen country codes and two cities, each one capital's coordinates.

    Calling the result regional is the misreading this sentence exists to
    block, and it would be made 174 days from now by someone reading only the
    number.
    """
    assert "at twenty-one named points" in text
    assert "may not be described as regional" in text


def test_the_aggregation_rule_is_fixed_with_its_reasons(text):
    assert "Mean, not the last observation of the day" in text
    assert "Not minimum or maximum" in text
    assert "An absent day is absent" in text


def test_the_coverage_threshold_is_the_one_the_gate_already_declares(text):
    """One number, not two that could drift apart."""
    from app.services.forecasting.entry_conditions import MIN_COVERAGE

    assert MIN_COVERAGE == 0.80
    assert "`MIN_COVERAGE`" in text
    assert "at least 19 of 24" in text


def test_the_clock_starts_at_the_merge_commit(text):
    """Not at the first measurement and not derived afterwards -- the same hole
    §9 was written to close for M2."""
    assert "At the merge commit of this file" in text
    assert "accumulation is data and not a milestone" in text


def test_the_earliest_evidential_dates_follow_the_gate(text):
    """Read from the gate, so the arithmetic in the document cannot drift from
    the constants it is derived from."""
    from app.services.forecasting.entry_conditions import (
        REQUIRED_WINDOWS, TRAINING_DAYS,
    )

    assert REQUIRED_WINDOWS == 12
    assert TRAINING_DAYS == {7: 90, 30: 180}
    assert f"{TRAINING_DAYS[7] + REQUIRED_WINDOWS * 7} days after" in text
    assert f"{TRAINING_DAYS[30] + REQUIRED_WINDOWS * 30} days after" in text


def test_it_does_not_claim_m2_showed_anything(text):
    assert "does not claim M2 demonstrated anything" in text
    assert "forbids the rename" in text


def test_the_amendment_record_exists_and_is_empty(text):
    """Empty is the correct state at version 1.0, and the section has to exist
    now -- adding it later, beside a first amendment, is how the record starts
    with the thing it was supposed to have caught."""
    assert "Amendment record" in text
    assert "None. This is version 1.0." in text
