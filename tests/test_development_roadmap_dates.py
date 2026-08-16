"""The roadmap's two dates are recomputed from code, never trusted as prose.

`docs/DEVELOPMENT_ROADMAP.md` is built around the claim that two dates cannot be
brought closer by working harder. That claim is only worth anything while the
dates match the constants the gate actually uses. If `TRAINING_DAYS` or
`REQUIRED_WINDOWS` is ever changed, this test fails and the document has to be
updated deliberately -- which is the point, because moving those constants moves
what the project is allowed to claim and when.

The same reason `tests/test_m3_declaration_is_recorded.py` exists: a document
nobody checks drifts exactly the way the README badge did, and prose that has
quietly stopped describing the code is worse than no document, because someone
plans against it.
"""
import os
import re
from datetime import date, timedelta

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROADMAP = os.path.join(REPO_ROOT, "docs", "DEVELOPMENT_ROADMAP.md")

#: The M3 clock start, declared in docs/M3_FORECAST_DECLARATION.md. Not derived
#: from anything in code, so it is stated once here and nowhere else.
CLOCK_START = date(2026, 8, 14)


def read_roadmap():
    if not os.path.exists(ROADMAP):
        pytest.fail("docs/DEVELOPMENT_ROADMAP.md is missing; the plan is the deliverable")
    with open(ROADMAP, encoding="utf-8") as handle:
        return handle.read()


def earliest_date(horizon):
    """What the gate's own constants imply, computed rather than restated."""
    from app.services.forecasting.entry_conditions import (
        REQUIRED_WINDOWS,
        TRAINING_DAYS,
    )

    days = TRAINING_DAYS[horizon] + REQUIRED_WINDOWS * horizon
    return CLOCK_START + timedelta(days=days)


@pytest.mark.parametrize("horizon", [7, 30])
def test_the_roadmap_states_the_date_the_gate_implies(horizon):
    body = read_roadmap()
    expected = earliest_date(horizon).isoformat()

    assert expected in body, (
        f"the roadmap does not state {expected} for horizon {horizon}. "
        "Either the gate constants moved and the document was not updated, or "
        "the document names a date the code does not support -- both are the "
        "failure this test exists to catch."
    )


def test_no_other_earliest_date_is_named():
    """A stale date left beside the correct one is the same defect, half-fixed."""
    body = read_roadmap()
    allowed = {earliest_date(7).isoformat(), earliest_date(30).isoformat()}
    #: Dates in 2027 or later can only be the two gate dates; anything else in
    #: that range is a leftover from an edit.
    found = set(re.findall(r"\b20(?:2[7-9]|[3-9]\d)-\d{2}-\d{2}\b", body))

    assert found <= allowed, f"unexpected future dates in the roadmap: {sorted(found - allowed)}"


#: Every plan that existed before consolidation. Kept, never deleted -- they
#: record why decisions were made -- but none of them may look current.
SUPERSEDED = (
    "ROADMAP.md",
    "ROADMAP_ENV_CRISIS_2026.md",
    "ROADMAP_DEFENSE_v6.md",
    "ROADMAP_DEFENSE_v6.2.md",
    "SORA_Earth_Roadmap.md",
)
HISTORICAL_BANNER = "HISTORICAL — not the current plan"


def test_exactly_one_document_declares_itself_current():
    """Six plans of equal apparent authority is what produced three stale reviews.

    `ROADMAP.md` announced itself as *Active development* while describing a
    sprint that finished months earlier, so a reader who found it first planned
    against a project that no longer existed. One document may claim to be
    current, and this is the check that keeps it one.
    """
    claiming = []
    for name in ("docs/DEVELOPMENT_ROADMAP.md",) + SUPERSEDED:
        path = os.path.join(REPO_ROOT, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as handle:
            head = handle.read(4000)
        if re.search(r"\*\*Status:\*\*\s*(CURRENT|Active)", head):
            claiming.append(name)

    assert claiming == ["docs/DEVELOPMENT_ROADMAP.md"], (
        f"expected exactly one current roadmap, found: {claiming}"
    )


@pytest.mark.parametrize("name", SUPERSEDED)
def test_every_superseded_plan_says_so_at_the_top(name):
    """The banner has to be the first thing read, not a note further down."""
    path = os.path.join(REPO_ROOT, name)
    if not os.path.exists(path):
        pytest.skip(f"{name} is no longer in the repository")
    with open(path, encoding="utf-8") as handle:
        head = handle.read(600)

    assert HISTORICAL_BANNER in head, (
        f"{name} does not carry the historical banner in its first lines; a "
        "reader who stops after the title will take it for the current plan"
    )


def test_the_threshold_it_quotes_is_the_one_in_force():
    """The roadmap says the 0.80 gate is already done. It must still be 0.80."""
    body = read_roadmap()
    scheduler = os.path.join(REPO_ROOT, "app", "scheduler.py")
    with open(scheduler, encoding="utf-8") as handle:
        source = handle.read()

    match = re.search(r"MIN_AUC_THRESHOLD\s*=\s*([0-9.]+)", source)
    assert match, "MIN_AUC_THRESHOLD is gone from app/scheduler.py"
    assert match.group(1) in body, (
        f"the roadmap quotes a threshold the code no longer uses ({match.group(1)})"
    )
