"""The declared region set, and the boundary it draws.

§1.4 of the M2 protocol requires the population to be an explicit enumeration
with a mapping version, fixed before the first snapshot rather than before the
first model run -- because the coverage denominators are meaningless without it.
It also forbids revisiting the membership later: deciding which regions count
after seeing which of them have data is selection on the outcome.

The measurement that made this concrete: `environmental_observations` holds 104
distinct ids, and that is not 85 regions plus 19 missing ones. It is two
populations of different kinds.

    canonical    85 Russian federal subjects, RU-*
    contextual   21 openmeteo entities, of which 19 are countries
                 (BRA CAN CHN DEU ESP FRA GBR IDN IND ITA JPN KEN KOR MEX
                  NGA NLD POL USA ZAF)
    overlap      2 -- RU-MOW and RU-SPE

So the boundary is not a filter on one population; it separates two. The
canonical writer refuses anything outside the declared set. The observation
layer keeps accepting both, because the protocol explicitly allows forecasting
the openmeteo entities as a separate product, and a guard there would delete
that data.
"""
import pytest

from app.services.esg_aggregator import (
    DECLARED_REGIONS,
    DECLARED_REGIONS_V1,
    REGION_SET_VERSION,
    UndeclaredRegionError,
    require_declared,
)


def test_the_set_is_versioned_and_enumerated():
    """A count is not a declaration; §1.4 asks for the ids and a version."""
    assert REGION_SET_VERSION == "ru-regions-v1"
    assert isinstance(DECLARED_REGIONS_V1, frozenset)
    assert len(DECLARED_REGIONS_V1) == 85
    assert DECLARED_REGIONS_V1 is DECLARED_REGIONS, (
        "the versioned name and the working alias have come apart, so two "
        "call sites can disagree about what the declared set is"
    )


def test_every_id_is_a_russian_subject():
    """The canonical population is one kind of thing.

    Asserted on the shape rather than by listing them again: a second copy of
    the list would drift from the first, and the drift test below is what keeps
    the membership honest.
    """
    assert all(r.startswith("RU-") for r in DECLARED_REGIONS_V1), (
        sorted(r for r in DECLARED_REGIONS_V1 if not r.startswith("RU-"))
    )


def test_both_canonical_sources_cover_exactly_the_declared_set():
    """The drift guard. A divergence has to become someone's decision.

    If a source quietly gains or loses a region, the declared set is no longer
    what either of them measures, and every coverage figure computed against it
    is wrong in a way nothing else would report.
    """
    from app.ingesters.sber_veb_baseline import BASELINE
    from data.rosstat_snapshot_2024 import UNEMPLOYMENT

    assert set(BASELINE) == DECLARED_REGIONS_V1, {
        "extra in sber": sorted(set(BASELINE) - DECLARED_REGIONS_V1),
        "missing from sber": sorted(DECLARED_REGIONS_V1 - set(BASELINE)),
    }
    assert set(UNEMPLOYMENT) == DECLARED_REGIONS_V1, {
        "extra in rosstat": sorted(set(UNEMPLOYMENT) - DECLARED_REGIONS_V1),
        "missing from rosstat": sorted(DECLARED_REGIONS_V1 - set(UNEMPLOYMENT)),
    }


# --- the boundary, from both sides ------------------------------------------


def test_the_canonical_writer_refuses_an_undeclared_id():
    """Refused, not filtered.

    Filtering makes the set follow the data: a region that stops reporting
    leaves the denominator quietly and coverage looks unchanged.
    """
    with pytest.raises(UndeclaredRegionError) as exc:
        require_declared("DEU")

    assert "ru-regions-v1" in str(exc.value)
    assert "DEU" in str(exc.value)


def test_a_declared_id_passes_through_unchanged():
    """Otherwise the guard could be satisfied by refusing everything."""
    assert require_declared("RU-MOW") == "RU-MOW"


def test_the_openmeteo_population_is_disjoint_except_for_two():
    """The measurement that shaped this: 104 ids, two populations, overlap 2.

    Recorded as a test because the shape is what justifies refusing at the
    canonical writer and *not* at the observation layer. If the sets were
    nested -- one a subset of the other -- a single guard would be right.
    """
    from app.ingesters.openmeteo import REGION_CAPITALS

    openmeteo = {code for code, _lat, _lon in REGION_CAPITALS}

    assert len(openmeteo) == 21
    assert openmeteo & DECLARED_REGIONS_V1 == {"RU-MOW", "RU-SPE"}
    assert len(openmeteo | DECLARED_REGIONS_V1) == 104
    non_russian = {r for r in openmeteo if not r.startswith("RU-")}
    assert len(non_russian) == 19, sorted(non_russian)


def test_the_observation_layer_still_accepts_the_other_population():
    """A guard in persistence would delete data the protocol allows.

    §10 of the protocol names forecasting the openmeteo entities as a
    legitimate separate product. Asserted against the write path rather than by
    reading persist.py, so moving the check there later fails here.
    """
    from app.ingesters.base import Signal
    from app.ingesters.persist import _signal_to_observation_dict

    from datetime import datetime, timezone

    row = _signal_to_observation_dict(
        Signal(region_code="DEU", source="openmeteo", metric="temperature",
               value=12.0, observed_at=datetime(2026, 8, 13, tzinfo=timezone.utc)),
        "openmeteo",
    )

    assert row["region_id"] == "DEU"


# --- the denominator --------------------------------------------------------


def test_coverage_is_measured_against_the_declared_set(monkeypatch):
    """`|declared| x |days|`, never the regions that happen to have data.

    The aggregator already walks the declared set rather than the rows, which
    is the property this pins: a region with nothing stays in the denominator
    and is reported missing, instead of vanishing from both sides of the
    fraction.
    """
    import inspect

    from app.services import esg_aggregator

    src = inspect.getsource(esg_aggregator.recalc_all_regions)

    assert "for region_code in sorted(DECLARED_REGIONS)" in src, (
        "the loop no longer walks the declared set, so a region with no data "
        "would leave the denominator instead of being counted as missing"
    )
