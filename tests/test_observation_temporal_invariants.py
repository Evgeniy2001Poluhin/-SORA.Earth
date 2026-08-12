"""The database itself has to refuse a row that cannot be described honestly.

`app/ingesters/temporal.py` validates before an INSERT, which gives a clear
error at the right place. It is not enough on its own: a migration, a fixture,
a repair script or a psql session writes straight past it. The rules therefore
exist twice on purpose -- once in the validator, once as CHECK constraints --
and these tests assert the second copy by writing rows and requiring the
database to reject them.

The state machine, which is also the table in `temporal.py`:

    kind                   event_time   period_start/end        source_revision
    observed               NOT NULL     both NULL               optional
    period                 NULL         both NOT NULL, s <= e   NOT NULL
    not_applicable         NULL         both NULL               NOT NULL
    legacy_ingestion_time  NOT NULL     both NULL               optional

`legacy_ingestion_time` is writable here although the validator refuses it:
the migration has to create such rows to label what already exists, and a
constraint that forbade them would make that migration impossible.

Every rejection is followed by a rollback. Without it the session stays in a
failed transaction and the next parametrised case raises on entry -- passing
for a reason that has nothing to do with what it claims to test.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database import Base, EnvironmentalObservation


T = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
P0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
P1 = datetime(2024, 12, 31, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    """A real database built from the model, so CHECKs are actually created."""
    engine = create_engine(f"sqlite:///{tmp_path / 'inv.db'}")
    # SQLite ignores CHECK constraints unless foreign_keys-style enforcement is
    # on by default; it does honour CHECK, but the pragma is set explicitly so
    # a silent no-op cannot make every rejection test pass vacuously.
    Base.metadata.create_all(bind=engine, tables=[EnvironmentalObservation.__table__])
    Session = sessionmaker(bind=engine)
    with Session() as s:
        yield s
    engine.dispose()


def _row(**kw):
    fields = dict(region_id="RU-MOW", indicator="esg", value=1.0,
                  source="s", source_record_id=None, temporal_kind="observed",
                  event_time=T, period_start=None, period_end=None,
                  source_revision=None)
    fields.update(kw)
    return EnvironmentalObservation(**fields)


def _insert(db, **kw):
    db.add(_row(**kw))
    db.flush()


# --- states the contract allows --------------------------------------------


@pytest.mark.parametrize("kw", [
    dict(temporal_kind="observed", event_time=T),
    dict(temporal_kind="observed", event_time=T, source_revision="rev:v1:a"),
    dict(temporal_kind="period", event_time=None, period_start=P0, period_end=P1,
         source_revision="rev:v1:a"),
    dict(temporal_kind="period", event_time=None, period_start=P0, period_end=P0,
         source_revision="rev:v1:a"),
    dict(temporal_kind="not_applicable", event_time=None, source_revision="rev:v1:a"),
    dict(temporal_kind="legacy_ingestion_time", event_time=T),
    dict(temporal_kind="legacy_ingestion_time", event_time=T, source_revision="rev:v1:a"),
], ids=["observed", "observed+rev", "period", "period-single-day",
        "not_applicable", "legacy", "legacy+rev"])
def test_a_valid_row_is_accepted(db, kw):
    """A constraint that rejects everything would pass every rejection test."""
    _insert(db, **kw)


# --- states it must refuse --------------------------------------------------


@pytest.mark.parametrize("kw,why", [
    (dict(temporal_kind="observed", event_time=None), "observed without a time"),
    (dict(temporal_kind="observed", event_time=T, period_start=P0, period_end=P1),
     "observed carrying a period"),
    (dict(temporal_kind="period", event_time=T, period_start=P0, period_end=P1,
          source_revision="r"), "period carrying an event_time"),
    (dict(temporal_kind="period", event_time=None, period_start=P0, period_end=None,
          source_revision="r"), "period missing its end"),
    (dict(temporal_kind="period", event_time=None, period_start=None, period_end=P1,
          source_revision="r"), "period missing its start"),
    (dict(temporal_kind="period", event_time=None, period_start=P1, period_end=P0,
          source_revision="r"), "period ending before it starts"),
    (dict(temporal_kind="period", event_time=None, period_start=P0, period_end=P1),
     "period without a revision"),
    (dict(temporal_kind="not_applicable", event_time=T, source_revision="r"),
     "not_applicable carrying an event_time"),
    (dict(temporal_kind="not_applicable", event_time=None, period_start=P0,
          period_end=P1, source_revision="r"), "not_applicable carrying a period"),
    (dict(temporal_kind="not_applicable", event_time=None),
     "not_applicable without a revision"),
    (dict(temporal_kind="legacy_ingestion_time", event_time=None),
     "legacy without the timestamp it exists to preserve"),
    (dict(temporal_kind="observed_maybe", event_time=T), "an unknown kind"),
])
def test_an_impossible_row_is_rejected_by_the_database(db, kw, why):
    """Asserted against the database, not the validator.

    A migration, a repair script or a psql session never calls
    `temporal.validate`, and those are exactly the paths that wrote the rows
    #121 is about.
    """
    with pytest.raises(IntegrityError):
        _insert(db, **kw)
    # Required: the session is in a failed transaction until this runs, and the
    # next case would raise on entry and "pass" for the wrong reason.
    db.rollback()


# --- the shape of the column definitions ------------------------------------


def test_the_columns_say_what_the_contract_needs():
    cols = {c.name: c for c in EnvironmentalObservation.__table__.columns}

    assert cols["event_time"].nullable is True, (
        "a column that cannot say 'there is no observation time' forces every "
        "writer to invent one -- which is #121"
    )
    assert cols["temporal_kind"].nullable is False
    assert cols["period_start"].nullable is True
    assert cols["period_end"].nullable is True
    assert cols["source_revision"].nullable is True


def test_the_checks_are_named():
    """Named so a migration can create and drop them by hand.

    An unnamed CHECK gets a generated name that differs between backends, and
    the migration then cannot refer to it.
    """
    named = {c.name for c in EnvironmentalObservation.__table__.constraints
             if c.name}

    assert any("temporal_kind" in n for n in named), (
        f"no named CHECK covering temporal_kind: {sorted(named)}"
    )


# --- where the two layers deliberately disagree -----------------------------


def test_the_database_accepts_a_legacy_row(db):
    """The migration has to create these to label what already exists."""
    _insert(db, temporal_kind="legacy_ingestion_time", event_time=T)


def test_the_application_refuses_to_create_one():
    """And ingestion must never produce a new one.

    The two layers disagree on purpose, and the disagreement is the contract:
    `legacy_ingestion_time` records that an ingestion time was once stored as
    an observation. It describes history. A row created today with that label
    would be a fresh lie in the shape of an old one.

    Stated as a test so the next person does not reconcile the layers in the
    wrong direction -- removing the refusal is easier than reading why it is
    there.
    """
    from app.ingesters.temporal import LEGACY, TemporalContractError, validate

    with pytest.raises(TemporalContractError):
        validate(LEGACY, event_time=T, period_start=None, period_end=None,
                 source_revision=None)


def test_every_constraint_name_fits_postgresql():
    """PostgreSQL truncates identifiers at 63 *bytes*, silently.

    Two names sharing a 63-byte prefix collide after truncation and the second
    CREATE fails during migration, not here.
    """
    for c in EnvironmentalObservation.__table__.constraints:
        if c.name:
            assert len(c.name.encode("utf-8")) <= 63, f"{c.name}"
    for i in EnvironmentalObservation.__table__.indexes:
        assert len(i.name.encode("utf-8")) <= 63, f"{i.name}"


def test_the_expected_checks_are_present_exactly():
    """Named as a set, so an added or renamed constraint is visible here."""
    from sqlalchemy import CheckConstraint as _CC

    got = {c.name for c in EnvironmentalObservation.__table__.constraints
           if isinstance(c, _CC) and c.name}

    assert got == {
        "ck_environmental_observations_temporal_kind_known",
        "ck_environmental_observations_temporal_kind_state",
    }, f"unexpected CHECK set: {sorted(got)}"


def test_the_kind_has_no_database_default():
    """A default here would assert 'measured' on anyone's behalf.

    Every other column may sensibly default. This one carries a claim about
    where a number came from, and a row that does not say must not be answered
    for -- that substitution, on `event_time`, is what #121 exists to remove.
    """
    col = EnvironmentalObservation.__table__.columns["temporal_kind"]

    assert col.server_default is None, (
        f"temporal_kind defaults to {col.server_default.arg!r}; a row that "
        f"omits its kind would be recorded as that without anyone deciding"
    )


def test_a_row_without_a_kind_is_rejected(db):
    """And the database enforces it, not just the absence of a default."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        _insert(db, temporal_kind=None, event_time=T)
    db.rollback()
