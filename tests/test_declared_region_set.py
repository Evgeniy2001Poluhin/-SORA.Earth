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
    require_declared,
)


def _agg():
    """The module as it is *now*.

    `tests/test_esg_aggregator_reads_the_live_source.py` calls
    `importlib.reload` on it, which rebinds every class it defines. An exception
    class imported at file scope then stops matching the one actually raised,
    and `pytest.raises` lets it through -- these two tests passed alone and
    failed in the suite for exactly that reason. Fetching at call time keeps the
    identity current whatever ran before.
    """
    from app.services import esg_aggregator

    return esg_aggregator


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


class _SpyDb:
    """Records every mutation, so "it refused" can be told from "it wrote".

    An exception proves the call raised. It does not prove nothing reached the
    database -- the write could have happened first.
    """

    def __init__(self):
        self.added = []
        self.queried = []

    def query(self, *a, **k):
        self.queried.append(a)
        result = _SpyDb._Query()
        return result

    def add(self, obj):
        self.added.append(obj)

    class _Query:
        def filter_by(self, **kw):
            return self

        def first(self):
            return None


def test_the_canonical_writer_refuses_an_undeclared_id():
    """The writer, not the helper.

    An earlier draft called `require_declared("DEU")` directly and was named
    after the writer. The guard was defined, tested and called from nowhere: an
    undeclared id would have been stored exactly as before, and this test would
    still have been green.
    """
    agg = _agg()

    db = _SpyDb()
    with pytest.raises(agg.UndeclaredRegionError) as exc:
        agg._write_score(db, "DEU", {"total_score": 50.0})

    assert "ru-regions-v1" in str(exc.value)
    assert "DEU" in str(exc.value)
    assert db.added == [], "the row was written before the refusal"
    assert db.queried == [], "the writer reached the database at all"


def test_the_staleness_writer_refuses_one_too():
    """The other path that touches a canonical row."""
    agg = _agg()

    db = _SpyDb()
    with pytest.raises(agg.UndeclaredRegionError):
        agg._mark_stale(db, "DEU", missing=["x"], stale=[])

    assert db.queried == []


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


def test_coverage_walks_every_declared_region_including_empty_ones(monkeypatch):
    """`|declared| x |days|`, measured by watching which ids are visited.

    The earlier version grepped the source for the loop, which a comment or
    dead code would satisfy. This records the ids the run actually classifies
    and requires exactly the declared set -- so a region with no data stays in
    the denominator and is reported missing, and an id that appears only in the
    rows cannot add itself to it.
    """
    from app.services import esg_aggregator as agg

    visited = []
    real = agg._classify_pairs

    def _spy(metrics, limit):
        return real(metrics, limit)

    monkeypatch.setattr(agg, "_classify_pairs", _spy)

    seen_regions = []
    real_get = dict.get

    class _Latest(dict):
        def get(self, key, default=None):
            seen_regions.append(key)
            return real_get(self, key, default)

    # One region with data, one undeclared id in the rows, and the rest absent.
    latest = _Latest({"RU-MOW": {}, "DEU": {}})
    monkeypatch.setattr(agg, "_latest_by_region",
                        lambda db: (latest, None, 0, {}))
    monkeypatch.setattr(agg, "SessionLocal", lambda: _NullSession())

    agg.recalc_all_regions()

    assert set(seen_regions) == set(DECLARED_REGIONS_V1), {
        "not visited": sorted(DECLARED_REGIONS_V1 - set(seen_regions)),
        "visited but undeclared": sorted(set(seen_regions) - DECLARED_REGIONS_V1),
    }
    assert "DEU" not in seen_regions, (
        "an id present only in the rows entered the denominator"
    )


class _NullSession:
    """Enough of a session for the loop to run without a database."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def query(self, *a, **k):
        return self

    def filter_by(self, **k):
        return self

    def first(self):
        return None

    def add(self, obj):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


# --- the membership itself --------------------------------------------------


def test_the_membership_is_pinned_not_just_counted():
    """A version and a count still allow one id to be swapped for another.

    If both source dictionaries were edited in the same commit, the drift test
    above would stay green and the population would have changed under an
    unchanged version -- every figure measured before and after would be
    comparable in appearance only.
    """
    import hashlib

    from app.services.esg_aggregator import REGION_SET_FINGERPRINT

    expected = "d11632909eea19d33181d274ed2692fb7fa8e35a41107ed07791edbc4f0a0895"

    assert REGION_SET_FINGERPRINT == expected, (
        f"the membership of {REGION_SET_VERSION} changed. That is a new "
        f"region_set_version, not an edit: results measured under the old one "
        f"are not comparable and are not recomputed."
    )
    assert REGION_SET_FINGERPRINT == hashlib.sha256(
        "\n".join(sorted(DECLARED_REGIONS_V1)).encode()).hexdigest()


def test_every_rosstat_map_covers_the_declared_set():
    """All five, not one.

    The drift test checked UNEMPLOYMENT alone. One correct map says nothing
    about the other four, and a region missing from any of them is a hole in
    the score for that region.
    """
    from data import rosstat_snapshot_2024 as snap

    maps = {
        "UNEMPLOYMENT": snap.UNEMPLOYMENT,
        "INCOME": snap.INCOME,
        "LIFE_EXP": snap.LIFE_EXP,
        "BUDGET_TRANSPARENCY": snap.BUDGET_TRANSPARENCY,
        "DIGITAL_GOV": snap.DIGITAL_GOV,
    }

    for name, mapping in maps.items():
        assert set(mapping) == DECLARED_REGIONS_V1, {
            "map": name,
            "extra": sorted(set(mapping) - DECLARED_REGIONS_V1),
            "missing": sorted(DECLARED_REGIONS_V1 - set(mapping)),
        }
