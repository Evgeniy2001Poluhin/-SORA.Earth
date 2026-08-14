"""The status document names what it supersedes, and its dates come from code.

Five planning documents were current at different times and none said so. On
2026-08-14 a status file from July listed the observation table, both ingesters
and the Phase 0 audit as outstanding; all three had shipped. A reader cannot
tell a stale plan from a live one by reading it.

So this file pins the two things that make the difference: that every stale
document is named as superseded, and that the dates driving the plan are read
from the gate rather than typed.
"""
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(REPO_ROOT, "docs", "WHERE_THE_PROJECT_STANDS.md")

SUPERSEDED = (
    "ROADMAP.md",
    "ROADMAP_DEFENSE_v6.md",
    "ROADMAP_DEFENSE_v6.2.md",
    "PROJECT_ANALYSIS_2026-07-17.md",
    "IMPROVEMENTS_2026-07-17.md",
)


@pytest.fixture(scope="module")
def text():
    return re.sub(r"\s+", " ", open(DOC, encoding="utf-8").read())


@pytest.mark.parametrize("name", SUPERSEDED)
def test_every_stale_plan_is_named(text, name):
    """By name, so a reader of the stale file can be pointed here."""
    assert name in text, name


@pytest.mark.parametrize("name", SUPERSEDED)
def test_the_superseded_files_still_exist(name):
    """Named, not deleted. They are the record of what was believed when."""
    assert os.path.exists(os.path.join(REPO_ROOT, name)), name


def test_the_phase_plan_is_not_superseded(text):
    """ROADMAP_ENV_CRISIS_2026.md is the experiment's plan and still stands;
    claiming otherwise would delete the only description of phases 2-8."""
    assert "does not supersede ROADMAP_ENV_CRISIS_2026.md" in text.replace(
        "does not   supersede", "does not supersede")


def test_the_dates_are_the_gate_arithmetic(text):
    """Typed dates drift from the constants they came from. These are checked
    against the gate, so the plan cannot outlive its own premise."""
    from datetime import date, timedelta

    from app.services.forecasting.entry_conditions import (
        REQUIRED_WINDOWS, TRAINING_DAYS,
    )

    start = date(2026, 8, 14)
    for horizon in (7, 30):
        due = start + timedelta(
            days=TRAINING_DAYS[horizon] + REQUIRED_WINDOWS * horizon)
        assert due.isoformat() in text, (horizon, due.isoformat())


def test_the_window_question_is_stated(text):
    """The document's reason for existing: 174 days in which no forecasting
    result can be evidential, and what is worth doing in them."""
    assert "no forecasting result can be evidential" in text


def test_the_monitoring_gap_is_recorded_not_claimed_closed(text):
    """The author of this file previously said /ingestion/attention covered
    per-day coverage. It does not -- it reports per-run verdicts."""
    assert "Known gap" in text
    assert "18 of 24" in text


def test_what_not_to_do_is_present(text):
    """The half that is easy to drop and expensive to omit."""
    for refusal in ("Do not build the crisis engine",
                    "Do not add model candidates",
                    "Do not add data sources for their own sake",
                    "Do not restate M2"):
        assert refusal in text, refusal


def test_it_says_what_would_make_it_stale(text):
    """Without this the file becomes the sixth confident plan with no expiry."""
    assert "What would make this file stale" in text
