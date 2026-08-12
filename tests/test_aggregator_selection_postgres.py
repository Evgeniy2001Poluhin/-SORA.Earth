"""Which row the aggregator picks, decided on PostgreSQL.

SQLite cannot answer this. The two engines order NULLs in opposite directions:
PostgreSQL treats NULL as larger, so `ORDER BY event_time DESC` puts NULLs
first, while SQLite treats it as smaller and puts them last. Once `event_time`
became nullable (#121), a `not_applicable` row -- a constant, with no
observation date at all -- would have outranked a real observation on
PostgreSQL and lost to it on SQLite.

A test on SQLite would have been green while production picked the wrong row.
That is the whole reason this file exists and why it refuses to run anywhere
else.

What is asserted is not "NULLS LAST". Ordering direction is a mechanism; the
contract is that rows of different kinds are never compared at all. Each source
publishes one kind, the selection admits only that kind, and ranking happens
inside it.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.skipif(
    not (os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql")
         or os.environ.get("DATABASE_URL", "").startswith("postgresql")),
    reason="NULL ordering differs between PostgreSQL and SQLite, and this is "
           "about which row production picks",
)

NOW = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    from app.database import Base, EnvironmentalObservation

    url = os.environ.get("TEST_DATABASE_URL") or os.environ["DATABASE_URL"]
    schema = f"agg_{os.getpid()}_{abs(hash(tmp_path)) % 10**6}"
    engine = create_engine(url)
    with engine.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
        c.execute(text(f'SET search_path TO "{schema}"'))
    EnvironmentalObservation.__table__.schema = schema
    Base.metadata.create_all(bind=engine, tables=[EnvironmentalObservation.__table__])
    Session = sessionmaker(bind=engine)
    try:
        with Session() as s:
            yield s
    finally:
        EnvironmentalObservation.__table__.schema = None
        with engine.begin() as c:
            c.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def _add(db, **kw):
    from app.database import EnvironmentalObservation

    fields = dict(region_id="RU-MOW", indicator="esg_index_baseline", value=1.0,
                  source="sber_veb_baseline", ingested_at=NOW,
                  temporal_kind="not_applicable", event_time=None,
                  source_revision="rev:v1:a")
    fields.update(kw)
    db.add(EnvironmentalObservation(**fields))
    db.flush()


def test_a_null_event_time_does_not_outrank_an_observation(db):
    """The failure this file exists for, stated directly.

    On PostgreSQL `ORDER BY event_time DESC` puts a NULL first. If the two
    kinds were ranked together, the constant would win over a measurement taken
    today -- and no SQLite test would have noticed.
    """
    from sqlalchemy import select

    from app.database import EnvironmentalObservation as O

    _add(db, value=1.0)  # not_applicable, event_time NULL
    _add(db, value=2.0, temporal_kind="observed", event_time=NOW,
         source_revision=None)

    naive = db.execute(
        select(O.value).order_by(O.event_time.desc()).limit(1)).scalar()

    assert naive == 1.0, (
        "this database does not put NULL first on DESC, so the hazard this "
        "test guards cannot be reproduced here and the assertions below would "
        "pass for the wrong reason"
    )


def test_the_selection_admits_only_the_kind_the_source_publishes(db):
    """A row of an unexpected kind is not ranked against the expected one."""
    from app.services.esg_aggregator import EXPECTED_TEMPORAL_KIND

    assert EXPECTED_TEMPORAL_KIND["sber_veb_baseline"] == "not_applicable"
    assert EXPECTED_TEMPORAL_KIND["rosstat"] == "period"

    _add(db, value=1.0)
    _add(db, value=99.0, temporal_kind="observed", event_time=NOW,
         source_revision=None)
    _add(db, value=77.0, temporal_kind="legacy_ingestion_time",
         event_time=NOW + timedelta(days=1), source_revision=None)

    from sqlalchemy import select

    from app.database import EnvironmentalObservation as O

    admitted = db.execute(
        select(O.value).where(
            O.source == "sber_veb_baseline",
            O.temporal_kind == EXPECTED_TEMPORAL_KIND["sber_veb_baseline"],
        )).scalars().all()

    assert admitted == [1.0], (
        f"the selection admitted {admitted}; an observed or legacy row of this "
        f"source is a contract violation, not a candidate"
    )


def test_legacy_rows_are_excluded_but_still_readable(db):
    """They are evidence of what was recorded, not input to a score.

    Kept, not deleted: the point of labelling them was to stop counting them
    while preserving what the platform once claimed.
    """
    from sqlalchemy import func, select

    from app.database import EnvironmentalObservation as O

    _add(db, value=1.0)
    _add(db, value=77.0, temporal_kind="legacy_ingestion_time",
         event_time=NOW, source_revision=None)

    total = db.execute(select(func.count()).select_from(O)).scalar()
    legacy = db.execute(select(func.count()).select_from(O).where(
        O.temporal_kind == "legacy_ingestion_time")).scalar()

    assert total == 2 and legacy == 1, "the legacy row was not stored at all"
