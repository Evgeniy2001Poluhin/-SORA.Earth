"""M2 is closed with a negative result, and that must stay readable as one.

The failure this guards against is not a mistake in the analysis. It is the
later document that cites M2 as validation -- "the ESG forecast was evaluated,
MAE 0" -- which is true of the numbers and false about the world. Against a
constant the last-value baseline is exact, so a benchmark that ran would have
produced exactly that, and it would have been a property of the data wearing the
shape of a result about a method.

So the closure is pinned on content, not on a heading: the verdict, the reason,
the fact that nothing was benchmarked, and the sentence M3 inherits.
"""
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTOCOL = os.path.join(REPO_ROOT, "docs", "M2_EVALUATION_PROTOCOL.md")


@pytest.fixture(scope="module")
def protocol():
    """Whitespace collapsed to one space.

    The document is hard-wrapped, so a phrase this file looks for can be split
    across a line break -- "static\nsnapshot" failed the first version of the
    unblocking-conditions test for a reason that had nothing to do with the
    conditions. A check that depends on where the prose happens to wrap is
    testing the formatter.
    """
    return re.sub(r"\s+", " ", open(PROTOCOL, encoding="utf-8").read())


def test_the_outcome_is_a_negative_result_not_a_benchmark(protocol):
    assert "M2 outcome CLOSED -- NEGATIVE RESULT" in protocol
    assert "Benchmark executed no" in protocol


def test_the_reason_is_the_absence_of_a_target(protocol):
    """Not "the model underperformed", which is what a reader assumes when a
    forecasting milestone closes without one."""
    assert "no qualifying temporal target" in protocol


def test_it_is_not_recorded_as_an_engineering_failure(protocol):
    """The preconditions were built: #121 vintage, #132 interpolation, §1.4
    region set. The data could not answer the question."""
    assert "Engineering failure no" in protocol


def test_the_m3_boundary_sentence_is_present(protocol):
    """One sentence, so a future document cannot cite M2 as validation without
    contradicting something written down."""
    assert "M3 does not claim that M2 demonstrated a forecastable temporal ESG" in protocol
    assert "M2 is closed with a negative result" in protocol


def test_the_zero_error_trap_is_explained(protocol):
    """The specific misreading, named in the document rather than left to be
    rediscovered."""
    assert "MAE = 0 is not a success" in protocol or "MAE = 0` is not" in protocol
    assert "property of the data" in protocol


def test_the_unblocking_conditions_are_all_five(protocol):
    """A partial list would let a source that meets three of them look
    qualifying."""
    section = protocol[protocol.index("What would unblock a re-attempt"):]
    section = section[:2000]

    for requirement in ("varying", "monthly or quarterly", "ru-regions-v1",
                        "vintage", "static snapshot"):
        assert requirement in section, requirement


def test_the_amendment_is_registered_with_its_reason(protocol):
    """§9 permits amendment only as a numbered version with a date and reason.

    An amendment that tightens a gate before any run is the safe case; one made
    quietly after results exist is what §9 exists to prevent, and the record is
    what tells them apart.
    """
    assert "version 1.1 (amended 2026-08-13 under §9)" in protocol
    assert "Amendment record (§9)" in protocol
    assert "No result existed under v1.0" in protocol


def test_the_movement_condition_names_what_does_not_count(protocol):
    """All four, because each one was a real way to manufacture a series."""
    section = protocol[protocol.index("### 7.1 Movement"):]
    section = section[:3000]

    assert "repeated write of the same period" in section
    assert "ingested_at" in section
    assert "source_revision` carrying the same value" in section
    assert "interpolated point" in section


def test_the_candidate_survey_covers_every_source(protocol):
    """The negative result rests on an exhausted set, not on one audited target."""
    section = protocol[protocol.index("### 10.4 Every candidate"):]
    section = section[:3000]

    for source in ("rosstat", "sber_veb_baseline", "openmeteo",
                   "openmeteo_air_quality", "country_indicator_history",
                   "region_esg_scores"):
        assert source in section, source

    # The dichotomy is the finding; a table without it is just numbers.
    assert "mutually exclusive" in section


def test_the_open_prerequisites_are_named_and_not_treated_as_blockers(protocol):
    assert "#84" in protocol and "#75" in protocol
    section = protocol[protocol.index("What remains separately open"):][:800]
    assert "Neither lifts this" in section
