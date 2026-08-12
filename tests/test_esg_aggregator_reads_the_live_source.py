"""The ESG aggregator must read the table that is being written, and say when it isn't.

Until #116 it read `region_signals`, which took its last row on 2026-07-30 when
the ingester runner moved to `persist_environmental_observations`. It kept
recomputing the same 85 scores from frozen data and returning `{"status": "ok"}`
after every run, for eight days, while the map served those scores as current.

Three separate properties here, and they fail independently:

  - the source is the live table, not the dead one
  - a source that is empty or stale is degraded, not a success
  - `regions_written` counts rows the database actually changed

The third exists because of the mechanism the last test pins down: when the
source has not moved, recomputed values are identical, SQLAlchemy emits no
UPDATE, and `onupdate=func.now()` never fires. `updated_at` therefore keeps
meaning "when the value last changed" and can never answer "did anything run".
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import Integer, MetaData, create_engine
from sqlalchemy.orm import sessionmaker

from app.database import (
    EnvironmentalObservation, RegionESGScore, RegionSignal,
)
from app.services import esg_aggregator


def _now():
    return datetime.now(timezone.utc)


def _create_for_sqlite(models, engine):
    """Create each model's table on SQLite, deriving the DDL from the model.

    `RegionESGScore.id` is `BigInteger, Identity(always=True)` -- correct for
    PostgreSQL and impossible on SQLite, which assigns a rowid only to an
    `INTEGER PRIMARY KEY` exactly. Creating straight from the model yields
    `id BIGINT NOT NULL` and every insert fails.

    The tables are copied from the models rather than written out here, so a
    column added to a model appears in the fixture automatically. Only the
    identity is traded for plain autoincrement; the real DDL is covered against
    PostgreSQL by tests/test_migration_region_esg_scores.py, and is not what
    these tests are about.
    """
    meta = MetaData()
    for model in models:
        table = model.__table__.to_metadata(meta)
        pk = table.c[list(model.__table__.primary_key.columns)[0].name]
        pk.identity = None
        pk.type = Integer()
        pk.autoincrement = True
    meta.create_all(bind=engine)


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/agg.db")
    _create_for_sqlite(
        (EnvironmentalObservation, RegionESGScore, RegionSignal), engine
    )
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(esg_aggregator, "SessionLocal", factory)
    return factory


def _temporal_fields(source, when):
    """The temporal columns for a source, as its ingester would emit them."""
    from app.services.esg_aggregator import EXPECTED_TEMPORAL_KIND

    kind = EXPECTED_TEMPORAL_KIND.get(source, "observed")
    if kind == "period":
        return dict(temporal_kind=kind, event_time=None,
                    period_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    period_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
                    source_revision="rev:v1:fixture")
    if kind == "not_applicable":
        return dict(temporal_kind=kind, event_time=None,
                    source_revision="rev:v1:fixture")
    return dict(temporal_kind=kind, event_time=when)


def _observe(session, region, source, indicator, value, event_time=None,
             valid=True, ingested_at=None):
    when = event_time or _now()
    session.add(EnvironmentalObservation(
        region_id=region,
        indicator=indicator,
        value=value,
        source=source,
        # Each source declares the kind it actually publishes: rosstat a
        # yearly snapshot, sber_veb_baseline a table of constants. Writing
        # `observed` for both would make the fixture describe a world the
        # ingesters do not produce, and the aggregator's selection -- which
        # admits only the expected kind -- would then be tested against rows
        # that cannot occur.
        **_temporal_fields(source, when),
        # Separate on purpose: for these sources `event_time` is `now` on every
        # run regardless of how old the numbers are, so only `ingested_at`
        # says whether the pipeline moved.
        ingested_at=ingested_at or when,
        is_valid=valid,
    ))


FULL_SET = (
    ("sber_veb_baseline", "esg_index_baseline", 70.0),
    ("rosstat", "unemployment_rate", 5.0),
    ("rosstat", "avg_income_rub", 75000.0),
    ("rosstat", "life_expectancy", 73.0),
    ("rosstat", "budget_transparency", 60.0),
    ("rosstat", "digital_gov_index", 80.0),
)


def _full_region(session, region="RU-MOS", event_time=None, base=70.0,
                 ingested_at=None, skip=()):
    """Every metric the formula requires, so a score can actually be produced.

    `skip` drops indicators, to build the incomplete region the contract must
    refuse.
    """
    for source, indicator, value in FULL_SET:
        if indicator in skip:
            continue
        if indicator == "esg_index_baseline":
            value = base
        _observe(session, region, source, indicator, value, event_time,
                 ingested_at=ingested_at)


def _all_declared(session, **kwargs):
    """Seed every declared region completely.

    The tests seed all 85 rather than one, because the completeness contract
    counts absent regions and a single-region fixture would be degraded by
    design. Passing the contract has to be possible, or the assertions about
    failing it prove nothing.
    """
    for region in sorted(esg_aggregator.DECLARED_REGIONS):
        _full_region(session, region=region, **kwargs)


def test_it_reads_the_live_observation_table(session_factory):
    """The point of #116. Reading region_signals, this finds nothing."""
    s = session_factory()
    _all_declared(s)
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions()

    assert result["status"] == "success", result
    assert result["regions_computed"] == 85
    assert result["observations_examined"] == 510

    s = session_factory()
    row = s.query(RegionESGScore).filter_by(region_code="RU-MOS").one()
    assert row.total_score is not None
    assert row.signals_used == 6
    s.close()


def test_the_dead_table_is_not_consulted(session_factory):
    """Rows in region_signals must not produce a score.

    Without this the fix could be "read both", which would keep serving frozen
    values for any region the live table has not reached yet.
    """
    s = session_factory()
    for source, metric, value in (
        ("sber_veb_baseline", "esg_index_baseline", 70.0),
        ("rosstat", "unemployment_rate", 5.0),
    ):
        s.add(RegionSignal(region_code="RU-SPE", source=source, metric=metric,
                           value=value, observed_at=_now(), created_at=_now()))
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions()

    assert result["regions_computed"] == 0
    assert result["status"] == "degraded"

    s = session_factory()
    assert s.query(RegionESGScore).count() == 0
    s.close()


def test_an_empty_source_is_degraded_not_a_success(session_factory):
    """Reported as success it is indistinguishable from a run that had data."""
    result = esg_aggregator.recalc_all_regions()

    assert result["status"] == "degraded"
    assert result["observations_examined"] == 0
    assert "no valid observations" in result["reason"]


def test_a_stalled_pipeline_is_degraded_not_a_success(session_factory):
    """Nothing ingested for 100h: the writer stopped, as it did for 8 days."""
    s = session_factory()
    _all_declared(s, ingested_at=_now() - timedelta(hours=100))
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions(max_ingest_age_hours=48)

    assert result["status"] == "degraded", result
    assert "ingestion silent" in result["reason"]
    assert result["pipeline_freshness"]["stalled"] is True
    assert result["pipeline_freshness"]["age_hours"] >= 99
    # Under the strict contract a stale pair is not admitted to a score, so
    # nothing is written. The earlier revision computed all 85 here, which is
    # exactly the behaviour the pair-level contract removed.
    assert result["required_pairs_stale"] == 510
    assert result["required_pairs_fresh"] == 0
    assert result["regions_computed"] == 0
    assert result["regions_incomplete"] == 85


def test_a_running_pipeline_is_not_degraded(session_factory):
    """The check must be able to pass, or it asserts nothing."""
    s = session_factory()
    _all_declared(s, ingested_at=_now() - timedelta(hours=1))
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions(max_ingest_age_hours=48)

    assert result["status"] == "success", result
    assert result["pipeline_freshness"]["stalled"] is False
    assert "reason" not in result


def test_an_old_event_time_does_not_stall_a_running_pipeline(session_factory):
    """The distinction this report exists to draw.

    A source may legitimately carry observations older than the bound while
    ingestion runs perfectly. Measuring staleness on `event_time` would call
    that a failure; measuring it on `ingested_at` does not.
    """
    s = session_factory()
    _all_declared(
        s,
        event_time=_now() - timedelta(days=400),
        ingested_at=_now() - timedelta(minutes=5),
    )
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions(max_ingest_age_hours=48)

    assert result["status"] == "success", result
    assert result["pipeline_freshness"]["stalled"] is False


def test_the_score_declares_itself_structural(session_factory):
    s = session_factory()
    _all_declared(s)
    s.commit()
    s.close()

    assert esg_aggregator.recalc_all_regions()["score_kind"] == "structural"


def test_a_fresh_row_does_not_make_old_information_look_recent(session_factory):
    """The provenance lie, inverted into the property that replaced it.

    This used to assert the vintage was *declared* in the aggregator, because
    `event_time` said `now` for numbers from 2024 and nothing in the database
    could contradict it. The rows can contradict it now: rosstat carries the
    period it describes, and the constants carry a revision instead of a date.

    Ingested a moment ago, and still reported as 2024 -- which is the whole
    point. What changed is that this comes off the row rather than from a
    mapping someone maintained by hand.
    """
    s = session_factory()
    _all_declared(s, event_time=_now(), ingested_at=_now())
    s.commit()
    s.close()

    vintage = esg_aggregator.recalc_all_regions()["source_data_vintage"]

    assert "2024" in vintage["rosstat"], vintage
    assert "no observation date" in vintage["sber_veb_baseline"], vintage
    assert "rev:" in vintage["sber_veb_baseline"], (
        "the constants report no revision, so two different snapshots would be "
        "indistinguishable in the output"
    )


def test_no_change_is_a_success_for_a_structural_index(session_factory):
    """regions_written == 0 is the expected steady state, not a fault.

    The inputs are static literals, so an unchanged score means the sources
    behaved exactly as they are built to. Reporting that as degraded would
    make the normal case indistinguishable from a broken one -- the same
    confusion, pointed the other way.
    """
    s = session_factory()
    _all_declared(s)
    s.commit()
    s.close()

    esg_aggregator.recalc_all_regions()
    second = esg_aggregator.recalc_all_regions()

    assert second["regions_written"] == 0
    assert second["status"] == "success", second
    assert second["regions_computed"] == 85


def test_invalid_observations_are_not_read(session_factory):
    s = session_factory()
    _full_region(s)
    _observe(s, "RU-MOS", "rosstat", "unemployment_rate", 99.0, valid=False)
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions()

    assert result["observations_examined"] == 6


def test_indicators_outside_the_required_set_are_not_read(session_factory):
    """openmeteo covers 21 regions the score table does not score.

    Reading everything would create score rows for country codes (BRA, CAN, ...)
    that carry no rosstat or sber_veb data at all.
    """
    s = session_factory()
    _full_region(s)
    _observe(s, "BRA", "openmeteo", "temperature", 28.0)
    _observe(s, "BRA", "openmeteo_air_quality", "pm2_5", 12.0)
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions()

    assert result["observations_examined"] == 6
    assert result["regions_computed"] == 1

    s = session_factory()
    assert s.query(RegionESGScore).filter_by(region_code="BRA").count() == 0
    s.close()


def test_the_newest_observation_wins(session_factory):
    s = session_factory()
    _full_region(s, event_time=_now() - timedelta(hours=5), base=50.0)
    _observe(s, "RU-MOS", "sber_veb_baseline", "esg_index_baseline", 90.0,
             event_time=_now() - timedelta(hours=1))
    s.commit()
    s.close()

    esg_aggregator.recalc_all_regions()

    s = session_factory()
    row = s.query(RegionESGScore).filter_by(region_code="RU-MOS").one()
    # env_score is the baseline, so it reads back the newer of the two.
    assert row.env_score == 90.0
    s.close()


def test_unchanged_values_emit_no_update_so_updated_at_cannot_show_a_run(session_factory):
    """The mechanism that made the eight-day staleness invisible.

    `RegionESGScore.updated_at` carries `onupdate=func.now()`, so the obvious
    reading is that any recomputation refreshes it. It does not: when every
    value is identical SQLAlchemy issues no UPDATE for the row, and a column
    default that fires on UPDATE cannot fire without one.

    This is pinned as a characterization test rather than fixed, because the
    behaviour is correct -- `updated_at` means "when the value last changed".
    What follows is that the job must report its own freshness, which is what
    `regions_written` and `newest_age_hours` are for.
    """
    s = session_factory()
    _full_region(s)
    s.commit()
    s.close()

    first = esg_aggregator.recalc_all_regions()
    assert first["regions_written"] == 1

    s = session_factory()
    stamp_after_first = s.query(RegionESGScore).one().updated_at
    s.close()

    second = esg_aggregator.recalc_all_regions()

    assert second["regions_computed"] == 1, "it still examined the region"
    assert second["regions_written"] == 0, "but changed nothing"

    s = session_factory()
    assert s.query(RegionESGScore).one().updated_at == stamp_after_first, (
        "no UPDATE was emitted, so onupdate did not fire -- which is why "
        "staleness has to be reported by the job itself"
    )
    s.close()


def test_a_changed_value_is_counted_as_written(session_factory):
    """regions_written must be able to be non-zero on a second run."""
    s = session_factory()
    _full_region(s, base=70.0)
    s.commit()
    s.close()

    esg_aggregator.recalc_all_regions()

    s = session_factory()
    _observe(s, "RU-MOS", "sber_veb_baseline", "esg_index_baseline", 85.0,
             event_time=_now() + timedelta(hours=1))
    s.commit()
    s.close()

    second = esg_aggregator.recalc_all_regions()

    assert second["regions_written"] == 1
    s = session_factory()
    assert s.query(RegionESGScore).one().env_score == 85.0
    s.close()


# --- configuration: a bad env value must not take down two services ---------


def test_a_bad_env_value_does_not_break_the_import(monkeypatch):
    """The blast radius, which is the whole point of parsing per run.

    `float(os.getenv(...))` at module scope turns a typo in a production
    environment variable into a failed import of both the backend and the
    scheduler, at startup, for a value consulted once per aggregation.
    """
    import importlib

    monkeypatch.setenv("SORA_AGGREGATOR_MAX_INGEST_AGE_HOURS", "fourty-eight")
    module = importlib.reload(esg_aggregator)

    assert module.recalc_all_regions is not None
    importlib.reload(esg_aggregator)  # restore for the rest of the session


def test_a_bad_env_value_fails_the_run_loudly(session_factory, monkeypatch):
    """Fail-fast, not a silent default.

    Substituting 48h would leave a deployment believing it had set a bound it
    had not. This is a production policy, not a convenience setting.
    """
    s = session_factory()
    _all_declared(s)
    s.commit()
    s.close()

    monkeypatch.setenv("SORA_AGGREGATOR_MAX_INGEST_AGE_HOURS", "fourty-eight")
    result = esg_aggregator.recalc_all_regions()

    assert result["status"] == "error"
    assert result["error_kind"] == "configuration"
    assert "SORA_AGGREGATOR_MAX_INGEST_AGE_HOURS" in result["error"]


@pytest.mark.parametrize("value", ["0", "-5", "nan", "inf", "abc"])
def test_unusable_bounds_are_rejected(value):
    with pytest.raises(esg_aggregator.AggregatorConfigError):
        esg_aggregator._parse_max_ingest_age_hours(value)


@pytest.mark.parametrize("value,expected", [("", 48.0), ("12", 12.0), ("0.5", 0.5)])
def test_usable_bounds_are_accepted(value, expected):
    """The rejection must be able to not fire, or it asserts nothing."""
    assert esg_aggregator._parse_max_ingest_age_hours(value) == expected


# --- completeness contract --------------------------------------------------


def test_rows_read_and_nothing_computed_is_degraded(session_factory):
    """`examined > 0, computed == 0` is the same defect one layer in.

    The source was read, the run produced no result, and reporting success
    would make that indistinguishable from a run that scored every region.
    """
    s = session_factory()
    for region in sorted(esg_aggregator.DECLARED_REGIONS):
        _observe(s, region, "sber_veb_baseline", "esg_index_baseline", 70.0)
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions()

    assert result["status"] == "degraded", result
    assert result["observations_examined"] == 85
    assert result["regions_computed"] == 0
    assert "read 85 observations and computed 0 regions" in result["reason"]


def test_an_incomplete_region_is_not_written(session_factory):
    """A partial score is indistinguishable from a complete one once stored.

    Before the gate, a region holding only `sber_veb_baseline` produced
    env = soc = gov = base -- a full-looking row from one input.
    """
    s = session_factory()
    _all_declared(s)
    # Commit before deleting: autoflush is off, so a bulk delete issued against
    # unflushed inserts would find nothing and quietly leave the region whole.
    s.commit()
    for _source, indicator, _v in FULL_SET:
        if indicator != "esg_index_baseline":
            s.query(EnvironmentalObservation).filter_by(
                region_id="RU-MOS", indicator=indicator
            ).delete()
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions()

    assert result["status"] == "degraded", result
    assert result["regions_incomplete"] == 1
    assert result["regions_computed"] == 84
    assert result["required_pairs_missing"] == 5
    assert "RU-MOS" in result["incomplete_regions"]

    s = session_factory()
    assert s.query(RegionESGScore).filter_by(region_code="RU-MOS").count() == 0
    s.close()


def test_a_region_missing_one_metric_is_still_incomplete(session_factory):
    """Five of six is not five-sixths of a score; it is a different score."""
    s = session_factory()
    _all_declared(s)
    s.commit()
    s.query(EnvironmentalObservation).filter_by(
        region_id="RU-SPE", indicator="life_expectancy"
    ).delete()
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions()

    assert result["regions_incomplete"] == 1
    assert result["incomplete_regions"]["RU-SPE"] == {
        "missing": ["rosstat:life_expectancy"]
    }


def test_a_region_absent_from_the_source_is_visible(session_factory):
    """Walking the table instead of the declared set cannot see this at all."""
    s = session_factory()
    _all_declared(s)
    s.commit()
    s.query(EnvironmentalObservation).filter_by(region_id="RU-KAM").delete()
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions()

    assert result["status"] == "degraded", result
    assert result["regions_absent"] == 1
    assert "RU-KAM" in result["absent_regions"]
    assert result["regions_computed"] == 84


def test_an_undeclared_region_is_reported_and_not_written(session_factory):
    """environmental_observations holds 19 country codes this index does not score."""
    s = session_factory()
    _all_declared(s)
    _full_region(s, region="BRA")
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions()

    assert result["regions_undeclared"] == 1
    assert "BRA" in result["undeclared_regions"]
    assert result["regions_computed"] == 85

    s = session_factory()
    assert s.query(RegionESGScore).filter_by(region_code="BRA").count() == 0
    s.close()


def test_the_declared_set_matches_both_sources(session_factory):
    """Drift guard for the declaration.

    DECLARED_REGIONS is written out rather than derived, so that a region
    leaving a source is a decision someone makes. This is what turns that
    into a decision instead of a surprise.
    """
    from app.ingesters.sber_veb_baseline import BASELINE

    assert set(esg_aggregator.DECLARED_REGIONS) == set(BASELINE)
    assert len(esg_aggregator.DECLARED_REGIONS) == 85


def test_the_vintage_says_where_it_came_from(session_factory):
    """The field exists so a reader can tell a fact from a hand-maintained one.

    It said `declared_in_aggregator` while the database could not hold a
    vintage. That was honest and it was a workaround. It now says the rows were
    read, and the mapping is gone rather than kept as a second answer to the
    same question -- two answers is how they drift.
    """
    s = session_factory()
    _all_declared(s)
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions()

    assert result["vintage_provenance"] == "derived_from_observation_rows"
    assert not hasattr(esg_aggregator, "SOURCE_DATA_VINTAGE"), (
        "the declared mapping is still there; it would be a second, silently "
        "diverging answer to the question the rows now answer"
    )


def test_the_formula_itself_refuses_an_incomplete_set():
    """Defence in depth, tested directly.

    The caller checks completeness before calling `_compute_one`, so this gate
    is shadowed and mutating it away turns nothing red. That makes it a guard
    with no evidence behind it, which is the state this suite exists to avoid.
    Asserted here so a future caller that forgets the check cannot slip a
    partial score through.
    """
    complete = {f"{src}:{ind}": (1.0, src) for src, ind in esg_aggregator.REQUIRED_METRICS}
    assert esg_aggregator._compute_one(complete) != {}

    for key in list(complete):
        partial = {k: v for k, v in complete.items() if k != key}
        assert esg_aggregator._compute_one(partial) == {}, (
            f"a set missing {key} must not produce a score"
        )


# --- pair-level freshness contract ------------------------------------------
#
# A global maximum over ingested_at proves that *something* is being written.
# It cannot see one required pair that stopped: 509 pairs arriving today and
# one a week old leaves the region formally complete, the global timestamp
# fresh, the week-old value in the score, and the run reporting success.


def test_one_stale_pair_out_of_510_degrades_the_run(session_factory):
    s = session_factory()
    _all_declared(s, ingested_at=_now() - timedelta(minutes=5))
    s.commit()
    stale = s.query(EnvironmentalObservation).filter_by(
        region_id="RU-TVE", indicator="unemployment_rate"
    ).one()
    stale.ingested_at = _now() - timedelta(days=7)
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions(max_ingest_age_hours=48)

    assert result["status"] == "degraded", result
    assert result["required_pairs_total"] == 510
    assert result["required_pairs_stale"] == 1
    assert result["required_pairs_fresh"] == 509
    assert result["regions_incomplete"] == 1
    assert result["regions_complete"] == 84
    assert result["incomplete_regions"]["RU-TVE"] == {
        "stale": ["rosstat:unemployment_rate"]
    }

    s = session_factory()
    assert s.query(RegionESGScore).filter_by(region_code="RU-TVE").count() == 0
    s.close()


def test_one_source_stopping_degrades_while_the_global_clock_looks_fresh(session_factory):
    """The exact scenario the global check cannot see.

    rosstat stops entirely; sber_veb_baseline keeps writing its baseline every
    day. `pipeline_freshness` stays green because something is still arriving.
    """
    s = session_factory()
    _all_declared(s, ingested_at=_now() - timedelta(days=7))
    s.commit()
    for row in s.query(EnvironmentalObservation).filter_by(
        source="sber_veb_baseline"
    ):
        row.ingested_at = _now() - timedelta(minutes=5)
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions(max_ingest_age_hours=48)

    # The diagnostic says everything is fine, and it is not wrong -- something
    # is being written. It is simply not the question that matters.
    assert result["pipeline_freshness"]["stalled"] is False
    assert result["status"] == "degraded", result
    assert result["required_pairs_stale"] == 425      # 85 regions x 5 rosstat
    assert result["required_pairs_fresh"] == 85       # 85 x sber_veb_baseline
    assert result["regions_complete"] == 0
    assert result["regions_incomplete"] == 85


def test_a_pair_without_an_ingested_at_is_not_counted_fresh(session_factory):
    """Unknown is not acceptable -- treating it so is how the defect worked.

    `EnvironmentalObservation.ingested_at` is NOT NULL, so this cannot arise
    from the database today. Asserted at the classifier so the guard has
    evidence behind it rather than being defence nobody has exercised.
    """
    metrics = {
        k: (1.0, k.split(":")[0], _now())
        for k in esg_aggregator.REQUIRED_METRIC_KEYS
    }
    missing, stale, fresh = esg_aggregator._classify_pairs(metrics, 48)
    assert (missing, stale, len(fresh)) == ([], [], 6)

    key = "rosstat:life_expectancy"
    metrics[key] = (1.0, "rosstat", None)
    missing, stale, fresh = esg_aggregator._classify_pairs(metrics, 48)
    assert missing == [key]
    assert len(fresh) == 5


def test_every_pair_fresh_is_a_success(session_factory):
    """The contract must be passable, or every assertion above proves nothing."""
    s = session_factory()
    _all_declared(s, ingested_at=_now() - timedelta(hours=1))
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions(max_ingest_age_hours=48)

    assert result["status"] == "success", result
    assert result["required_pairs_total"] == 510
    assert result["required_pairs_fresh"] == 510
    assert result["required_pairs_stale"] == 0
    assert result["required_pairs_missing"] == 0
    assert result["regions_complete"] == 85


def test_an_undeclared_region_does_not_pad_the_denominator(session_factory):
    """510 is 85 declared x 6 required, whatever else the table holds."""
    s = session_factory()
    _all_declared(s, ingested_at=_now() - timedelta(hours=1))
    _full_region(s, region="BRA", ingested_at=_now() - timedelta(hours=1))
    _full_region(s, region="CAN", ingested_at=_now() - timedelta(hours=1))
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions(max_ingest_age_hours=48)

    assert result["required_pairs_total"] == 510
    assert result["required_pairs_fresh"] == 510
    assert result["regions_undeclared"] == 2
    assert result["status"] == "success", result

    s = session_factory()
    assert s.query(RegionESGScore).count() == 85
    s.close()


# --- a score that stopped being backed must say so on the row ---------------
#
# The aggregator refuses to overwrite a score whose region went incomplete --
# recomputing from a partial set would replace a complete value with a worse
# one. But an untouched row is indistinguishable from a current score to
# anything reading region_esg_scores directly, which app/routes/map_russia.py
# does. That is #116 at the consumer boundary rather than the producer's.


def _row(session_factory, region="RU-MOS"):
    s = session_factory()
    try:
        return s.query(RegionESGScore).filter_by(region_code=region).one()
    finally:
        s.close()


def test_a_region_that_goes_incomplete_is_marked_on_the_row(session_factory):
    s = session_factory()
    _all_declared(s)
    s.commit()
    s.close()
    esg_aggregator.recalc_all_regions()

    before = _row(session_factory)
    assert before.stale_since is None
    score_before = before.total_score

    # Push updated_at into the past explicitly. Without this the assertion
    # below cannot fail: SQLite renders onupdate as CURRENT_TIMESTAMP at
    # second resolution, the test finishes inside one second, and the value
    # matches whether or not it was preserved.
    updated_before = _now() - timedelta(days=3)
    s = session_factory()
    row = s.query(RegionESGScore).filter_by(region_code="RU-MOS").one()
    row.updated_at = updated_before
    s.query(EnvironmentalObservation).filter_by(
        region_id="RU-MOS", indicator="life_expectancy"
    ).delete()
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions()
    assert result["status"] == "degraded"
    assert result["regions_incomplete"] == 1

    after = _row(session_factory)
    assert after.stale_since is not None, (
        "the row still reads as a current score, which is the whole defect"
    )
    assert "life_expectancy" in after.stale_reason
    assert after.total_score == score_before, (
        "the score must be preserved, not recomputed from a partial set"
    )
    stored = after.updated_at
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=timezone.utc)
    assert abs((stored - updated_before).total_seconds()) < 2, (
        "updated_at means 'when the score changed'; marking staleness is not "
        f"a change of score, but it moved to {stored}"
    )


def test_stale_since_records_when_it_started_not_when_it_was_last_checked(session_factory):
    s = session_factory()
    _all_declared(s)
    s.commit()
    s.close()
    esg_aggregator.recalc_all_regions()

    s = session_factory()
    s.query(EnvironmentalObservation).filter_by(
        region_id="RU-MOS", indicator="life_expectancy"
    ).delete()
    s.commit()
    s.close()

    esg_aggregator.recalc_all_regions()
    first = _row(session_factory).stale_since
    esg_aggregator.recalc_all_regions()
    second = _row(session_factory).stale_since

    assert first == second, "'stale since Tuesday' is actionable; 'as of now' is not"


def test_the_mark_is_cleared_when_the_region_is_backed_again(session_factory):
    s = session_factory()
    _all_declared(s)
    s.commit()
    s.close()
    esg_aggregator.recalc_all_regions()

    s = session_factory()
    removed = s.query(EnvironmentalObservation).filter_by(
        region_id="RU-MOS", indicator="life_expectancy"
    ).one()
    value, source, event_time = removed.value, removed.source, removed.event_time
    s.delete(removed)
    s.commit()
    s.close()
    esg_aggregator.recalc_all_regions()
    assert _row(session_factory).stale_since is not None

    s = session_factory()
    _observe(s, "RU-MOS", source, "life_expectancy", value, event_time)
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions()

    assert result["status"] == "success", result
    assert result["regions_unmarked_stale"] == 1
    row = _row(session_factory)
    assert row.stale_since is None
    assert row.stale_reason is None


def test_a_stale_pair_marks_the_row_too(session_factory):
    """Not only a missing metric -- an old one has the same consequence."""
    s = session_factory()
    _all_declared(s, ingested_at=_now() - timedelta(minutes=5))
    s.commit()
    s.close()
    esg_aggregator.recalc_all_regions(max_ingest_age_hours=48)

    s = session_factory()
    row = s.query(EnvironmentalObservation).filter_by(
        region_id="RU-TVE", indicator="unemployment_rate"
    ).one()
    row.ingested_at = _now() - timedelta(days=7)
    s.commit()
    s.close()

    esg_aggregator.recalc_all_regions(max_ingest_age_hours=48)

    marked = _row(session_factory, "RU-TVE")
    assert marked.stale_since is not None
    assert "stale" in marked.stale_reason
    assert "unemployment_rate" in marked.stale_reason


def test_a_region_that_vanishes_entirely_is_marked(session_factory):
    s = session_factory()
    _all_declared(s)
    s.commit()
    s.close()
    esg_aggregator.recalc_all_regions()

    s = session_factory()
    s.query(EnvironmentalObservation).filter_by(region_id="RU-KAM").delete()
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions()

    assert result["regions_absent"] == 1
    marked = _row(session_factory, "RU-KAM")
    assert marked.stale_since is not None
    assert marked.total_score is not None, "the last known score is preserved"


def test_a_region_never_written_needs_no_mark(session_factory):
    """Nothing can be misread if nothing was written."""
    s = session_factory()
    for region in sorted(esg_aggregator.DECLARED_REGIONS):
        _observe(s, region, "sber_veb_baseline", "esg_index_baseline", 70.0)
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions()

    assert result["regions_computed"] == 0
    s = session_factory()
    assert s.query(RegionESGScore).count() == 0
    s.close()
