"""The two sources that emit literals must not claim an observation time.

`sber_veb_baseline` is a dict of 85 constants and `rosstat` reads an offline
2024 snapshot; neither makes a network call. Both stamped `datetime.now()` on
every value, and persistence recorded that as when the number was observed
(#121). The stamp was also part of the deduplication key, so each run produced
a new identity and inserted the same unchanged constant again.

What each declares now, and why it is not the other:

* sber -- `not_applicable`. There is no observation date at all; the value is a
  constant someone chose. A period would be a guess about when it applied.
* rosstat -- `period`, bounded by the reference year. A date inside 2024 would
  replace one false precision with another, which is the defect being removed.

The revision is a content hash in both cases. A hand-written version fails the
way version numbers always fail: someone edits a constant, forgets to bump it,
and the new value collapses onto the old identity and disappears with no error.
"""
import asyncio
from datetime import timezone

import pytest

from app.ingesters import temporal
from app.ingesters.rosstat import PERIOD_END, PERIOD_START, RosstatIngester
from app.ingesters.sber_veb_baseline import BASELINE, SberVebBaselineIngester


def _fetch(ingester):
    return asyncio.get_event_loop().run_until_complete(ingester.fetch())


@pytest.fixture(scope="module")
def sber():
    return _fetch(SberVebBaselineIngester())


@pytest.fixture(scope="module")
def rosstat():
    return _fetch(RosstatIngester())


# --- neither invents an observation time ------------------------------------


@pytest.mark.parametrize("name", ["sber", "rosstat"])
def test_no_signal_carries_an_observation_time(name, request):
    """The defect in one assertion, for both sources."""
    signals = request.getfixturevalue(name)

    assert signals, f"{name} emitted nothing; the assertion would be vacuous"
    stamped = [s for s in signals if s.observed_at is not None]
    assert stamped == [], (
        f"{len(stamped)} {name} signal(s) carry an observation time for values "
        f"that were never observed"
    )


def test_every_signal_passes_the_temporal_contract(sber, rosstat):
    """Whatever each declares has to be internally consistent.

    Run through the same validator persistence uses, so a source cannot emit a
    combination that would be refused at write time -- the failure would
    otherwise appear as a rejected batch in production rather than here.
    """
    for s in sber + rosstat:
        temporal.validate(
            s.temporal_kind, event_time=s.observed_at,
            period_start=s.period_start, period_end=s.period_end,
            source_revision=s.source_revision,
        )


# --- each declares the right kind, and only that ----------------------------


def test_sber_is_not_applicable_with_no_period(sber):
    """A constant has no observation date and no interval either. Asserting the
    period is absent matters: `period` would be a guess about when the value
    held, and nothing in the source says that."""
    assert {s.temporal_kind for s in sber} == {temporal.NOT_APPLICABLE}
    assert all(s.period_start is None and s.period_end is None for s in sber)
    assert len(sber) == len(BASELINE)


def test_rosstat_is_a_bounded_period_not_a_date(rosstat):
    assert {s.temporal_kind for s in rosstat} == {temporal.PERIOD}
    assert {s.period_start for s in rosstat} == {PERIOD_START}
    assert {s.period_end for s in rosstat} == {PERIOD_END}
    assert PERIOD_START.year == PERIOD_END.year == 2024
    assert PERIOD_START.tzinfo is not None and PERIOD_END.tzinfo is not None
    assert (PERIOD_END - PERIOD_START).days >= 365, (
        "the period does not span the reference year; a narrower range would "
        "claim precision the snapshot does not have"
    )


# --- the revision is a fact about the content -------------------------------


@pytest.mark.parametrize("name", ["sber", "rosstat"])
def test_one_revision_per_source_and_it_is_a_hash(name, request):
    signals = request.getfixturevalue(name)
    revisions = {s.source_revision for s in signals}

    assert len(revisions) == 1, f"{name} emitted {len(revisions)} revisions"
    only = revisions.pop()
    assert only.startswith("rev:"), only
    assert len(only) > 40, "too short to be a digest"


def test_two_runs_of_an_unchanged_snapshot_agree():
    """Otherwise every run would be a new revision and a new row."""
    first = {s.source_revision for s in _fetch(SberVebBaselineIngester())}
    second = {s.source_revision for s in _fetch(SberVebBaselineIngester())}

    assert first == second


def test_editing_one_constant_moves_the_revision(monkeypatch):
    """The property a hand-written version cannot provide.

    Someone changing a number and forgetting to bump a version would have the
    new value collapse onto the old identity and vanish silently. Asserted by
    changing one of 85 values: the revision covers the whole snapshot, so every
    row belongs to a new version of the set even though its own number did not
    move.
    """
    import app.ingesters.sber_veb_baseline as mod

    before = {s.source_revision for s in _fetch(SberVebBaselineIngester())}
    edited = dict(BASELINE)
    edited["RU-MOW"] = edited["RU-MOW"] + 0.1
    monkeypatch.setattr(mod, "BASELINE", edited)
    after = {s.source_revision for s in _fetch(SberVebBaselineIngester())}

    assert before != after, (
        "one changed constant produced the same revision, so the edit would be "
        "discarded as a duplicate of the old snapshot"
    )
