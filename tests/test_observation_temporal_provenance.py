"""Persistence must not invent an observation time it was not given.

`_signal_to_observation_dict` substitutes `datetime.now()` whenever a signal
carries no `observed_at` (#121). Two ingesters emit nothing but literals --
`sber_veb_baseline` a dict of 85 constants, `rosstat` an offline 2024 snapshot --
and every run stamps them with today, so the database records that a constant
was *observed* today when what happened is that a `.py` file was re-read.

The stamp does a second, less visible thing. `source_record_id` is built from
`event_time.isoformat()` and carries the partial unique index, so a new stamp
means a new identity: the conflict never fires and the same unchanged constant
is inserted again on every run. The false timestamp is not only a claim about
freshness, it is why the table grows.

Which is why these tests live at the persistence layer rather than on the two
ingesters. An ingester corrected to emit `observed_at=None` would have `now`
written for it here regardless -- its own test would pass, and the row would
still be wrong.

The temporal contract being restored, four kinds rather than one column:

    observed               a real point in time      event_time required
    period                 a reporting interval      bounds + revision required
    not_applicable         a static literal          revision required
    legacy_ingestion_time  what was recorded before  kept as evidence

`period` deliberately carries bounds instead of a date inside the range: naming
2024-01-01 as "the 2024 snapshot" replaces one false precision with another.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.ingesters.base import Signal
from app.ingesters.persist import _signal_to_observation_dict


T0 = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)


def _static(value=89.0, revision="obs:v2:deadbeef", **kw):
    """A signal from a source that has no observation time at all."""
    fields = dict(
        region_code="RU-MOW",
        source="sber_veb_baseline",
        metric="esg_index_baseline",
        value=value,
        unit="0-100",
        observed_at=None,
    )
    fields.update(kw)
    sig = Signal(**fields)
    # Set through attributes so this file states the contract it wants rather
    # than depending on the order of a dataclass signature that is about to
    # gain fields.
    sig.temporal_kind = "not_applicable"
    sig.source_revision = revision
    return sig


def _at(when):
    """Freeze the clock persistence would otherwise read."""
    return patch("app.ingesters.persist.datetime", **{
        "now.return_value": when,
        "side_effect": lambda *a, **k: datetime(*a, **k),
    })


# --- the substitution itself ------------------------------------------------


def test_a_missing_observation_time_does_not_become_now():
    """The defect in one assertion."""
    row = _signal_to_observation_dict(_static(), "sber_veb_baseline")

    assert row["event_time"] is None, (
        "persistence invented an observation time for a static literal; the "
        "row now claims the constant was measured today"
    )


def test_a_static_row_is_not_reidentified_when_the_clock_moves():
    """Why the false stamp also breaks deduplication.

    Ten runs a day apart produced ten identities and therefore ten rows, all
    holding the same constant. The clock is moved between runs on purpose: two
    calls in the same second would collide by accident and the old code would
    look correct for a reason that has nothing to do with the fix.
    """
    ids = set()
    for day in range(10):
        with _at(T0 + timedelta(days=day)):
            ids.add(_signal_to_observation_dict(_static(), "sber_veb_baseline")
                    ["source_record_id"])

    assert len(ids) == 1, (
        f"ten runs of one unchanged revision produced {len(ids)} identities; "
        f"each is a duplicate row of the same constant"
    )


def test_a_changed_revision_is_a_different_record():
    """The other half: collapsing must not hide an edit.

    A hand-written version can be forgotten when a constant changes, and the new
    value would then collapse onto the old identity and vanish. The revision is
    a content hash for exactly this reason, so this asserts the identity moves
    with it.
    """
    # One frozen instant for both. Without it the two calls read the clock a few
    # microseconds apart, the identities differ for that reason alone, and the
    # test passes against the very code it exists to reject.
    with _at(T0):
        a = _signal_to_observation_dict(_static(revision="obs:v2:aaaa"), "sber_veb_baseline")
        b = _signal_to_observation_dict(_static(revision="obs:v2:bbbb"), "sber_veb_baseline")

    assert a["source_record_id"] != b["source_record_id"], (
        "two revisions share an identity, so an edited constant would be "
        "silently discarded as a duplicate"
    )


def test_the_revision_reaches_the_row():
    """`source_revision` is hardcoded to None in persistence today, so nothing
    an ingester sets can ever be stored."""
    row = _signal_to_observation_dict(_static(revision="obs:v2:cafe"), "sber_veb_baseline")

    assert row["source_revision"] == "obs:v2:cafe"


# --- the kinds --------------------------------------------------------------


def test_a_period_is_stored_with_bounds_and_no_event_time():
    """Rosstat 2024 is an interval. A date inside it would be invented."""
    sig = Signal(region_code="RU-MOW", source="rosstat", metric="grp_per_capita",
                 value=1.0, observed_at=None)
    sig.temporal_kind = "period"
    sig.period_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    sig.period_end = datetime(2024, 12, 31, tzinfo=timezone.utc)
    sig.source_revision = "obs:v2:snapshot2024"

    row = _signal_to_observation_dict(sig, "rosstat")

    assert row["event_time"] is None, "a period was collapsed to a point in time"
    assert row["period_start"] == sig.period_start
    assert row["period_end"] == sig.period_end
    assert row["temporal_kind"] == "period"


def test_an_observed_signal_is_unchanged():
    """The path that was always honest must not move.

    openmeteo carries real observation times, and they are the only truthful
    timestamps in the table. A fix that reclassified them would destroy the
    thing it is protecting.
    """
    sig = Signal(region_code="RU-MOW", source="openmeteo", metric="temperature",
                 value=21.5, observed_at=T0)
    sig.temporal_kind = "observed"

    row = _signal_to_observation_dict(sig, "openmeteo")

    assert row["event_time"] == T0
    assert row["temporal_kind"] == "observed"
    assert row["period_start"] is None and row["period_end"] is None


def test_two_observations_at_different_times_stay_distinct():
    """Deduplication for real observations keeps working as before."""
    a = Signal(region_code="RU-MOW", source="openmeteo", metric="temperature",
               value=21.5, observed_at=T0)
    b = Signal(region_code="RU-MOW", source="openmeteo", metric="temperature",
               value=22.0, observed_at=T0 + timedelta(hours=1))
    a.temporal_kind = b.temporal_kind = "observed"

    assert (_signal_to_observation_dict(a, "openmeteo")["source_record_id"]
            != _signal_to_observation_dict(b, "openmeteo")["source_record_id"])


# --- combinations that must be refused --------------------------------------


@pytest.mark.parametrize("kind,fields,why", [
    ("observed",       {"observed_at": None},
     "an observation with no time"),
    ("period",         {"period_start": None, "period_end": None,
                        "source_revision": "obs:v2:x"},
     "a period with no bounds"),
    ("period",         {"period_start": datetime(2024, 1, 1, tzinfo=timezone.utc),
                        "period_end": datetime(2024, 12, 31, tzinfo=timezone.utc),
                        "source_revision": None},
     "a period with no revision"),
    ("not_applicable", {"source_revision": None},
     "a static value with no revision"),
])
def test_an_impossible_temporal_combination_is_refused(kind, fields, why):
    """Rejected rather than filled in. Substituting a default is what created
    this issue; a refusal is visible and a default is not."""
    sig = Signal(region_code="RU-MOW", source="s", metric="m", value=1.0,
                 observed_at=fields.get("observed_at"))
    sig.temporal_kind = kind
    sig.period_start = fields.get("period_start")
    sig.period_end = fields.get("period_end")
    sig.source_revision = fields.get("source_revision")

    with pytest.raises(ValueError):
        _signal_to_observation_dict(sig, "s")
    # The message is not asserted: pinning wording makes the test about the
    # string. What matters is that it refuses rather than writes.


def test_isoformat_is_never_called_on_a_missing_time():
    """`None.isoformat()` was the crash waiting behind a nullable event_time.

    Asserted by running the static path, which is the one that has no time --
    an AttributeError here is the failure, not an error in the test.
    """
    row = _signal_to_observation_dict(_static(), "sber_veb_baseline")

    assert row["source_record_id"], "no identity was produced at all"
    assert "None" not in row["source_record_id"], (
        "the identity embeds the string 'None', so a missing time was "
        "stringified rather than handled"
    )
