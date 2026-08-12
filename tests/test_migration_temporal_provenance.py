"""The provenance migration, against a real PostgreSQL built by Alembic.

Every case starts from `d3f0a71c9b48` -- the parent revision, and the one
production is on -- rather than from `Base.metadata.create_all()`. The point of
this file is the transformation of an existing schema, and metadata says
nothing about what the old one looked like.

PostgreSQL, not SQLite. The migration's DDL, its transactional behaviour and
its downgrade are what production will run, and SQLite differs in all three. A
green SQLite run would be a statement about a database nobody deploys.

One recurring trap deserves naming, because it shaped the design here: after a
failed upgrade the schema looks untouched whether the migration refused before
touching it or ran `ALTER TABLE` and rolled back. PostgreSQL's transactional
DDL makes those two outcomes identical from the outside. So the ordering is
asserted by recording the statements the migration actually issued, not by
inspecting the schema afterwards.
"""
import os

import pytest
from sqlalchemy import create_engine, event, inspect, text

pytestmark = pytest.mark.skipif(
    not (os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql")
         or os.environ.get("DATABASE_URL", "").startswith("postgresql")),
    reason="the migration's DDL, transaction and downgrade behaviour are "
           "PostgreSQL-specific; SQLite would prove nothing about production",
)

PARENT = "d3f0a71c9b48"
TARGET = "c7e94b2a83f1"

#: Measured on production 2026-08-12. Counts are context, not expectations --
#: the migration must work for a table of any size holding these sources.
MEASURED = {
    "openmeteo": "observed",
    "openmeteo_air_quality": "observed",
    "rosstat": "legacy_ingestion_time",
    "sber_veb_baseline": "legacy_ingestion_time",
}
#: Registered, semantics known, zero rows on production. Classified anyway.
REGISTERED_UNMEASURED = {"openaq": "observed"}
#: Appears in code for a different table. A row of it here must stop the run.
DELIBERATELY_UNKNOWN = "world_bank"


def _url():
    return (os.environ.get("TEST_DATABASE_URL")
            or os.environ["DATABASE_URL"])


@pytest.fixture
def at_parent(tmp_path):
    """A database upgraded exactly to the parent revision, and torn down after.

    A schema of its own per test: reusing one leaks the previous case's rows
    into the next, and a classification assertion would then pass on data it
    did not create.
    """
    from alembic import command
    from alembic.config import Config

    schema = f"mig_{os.getpid()}_{abs(hash(tmp_path)) % 10**6}"
    engine = create_engine(_url())
    with engine.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))

    # One connection, handed to Alembic. `cfg.attributes["schema"]` did not
    # work: Alembic builds its own engine from sqlalchemy.url, so a search_path
    # set here reached nothing, the migration ran in `public`, and every
    # assertion inspected an empty schema. The recorder was on this engine and
    # saw no migration statement either.
    connection = engine.connect()
    connection.execute(text(f'SET search_path TO "{schema}"'))
    connection.commit()

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", _url())
    cfg.attributes["connection"] = connection
    command.upgrade(cfg, PARENT)

    yield engine, cfg, schema

    # Rollback before close: a case that ended mid-transaction would otherwise
    # hold locks and DROP SCHEMA would wait on itself.
    connection.rollback()
    connection.close()
    with engine.begin() as c:
        c.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _rows(engine, schema):
    """The fields that must survive the migration byte for byte."""
    with engine.begin() as c:
        return {r[0]: (r[1], r[2], r[3]) for r in c.execute(text(
            f'SELECT id, source, source_record_id, event_time '
            f'FROM "{schema}".environmental_observations ORDER BY id'))}


def _seed(engine, schema, sources):
    from datetime import datetime, timezone
    with engine.begin() as c:
        for i, s in enumerate(sources):
            c.execute(text(
                f'INSERT INTO "{schema}".environmental_observations '
                '(region_id, indicator, value, source, source_record_id, '
                ' event_time, ingested_at, is_valid) VALUES '
                '(:r, :i, :v, :s, :rid, :et, :et, true)'),
                dict(r="RU-MOW", i="esg", v=1.0, s=s, rid=f"{s}_esg_x{i}",
                     et=datetime(2026, 8, 1, 12, tzinfo=timezone.utc)))


class Recorder:
    """Every statement the migration issues, in order.

    Attached to the connection Alembic uses, because the question -- did a
    refusal happen before any DDL -- cannot be answered by looking at the
    schema afterwards. PostgreSQL rolls DDL back, so "refused first" and "ran
    ALTER TABLE then rolled back" leave the same database.
    """

    def __init__(self, engine):
        self.statements = []
        event.listen(engine, "before_cursor_execute", self._on)
        self._engine = engine

    def _on(self, conn, cursor, statement, params, context, executemany):
        self.statements.append(" ".join(statement.split()).upper())

    def stop(self):
        event.remove(self._engine, "before_cursor_execute", self._on)

    def any(self, *needles):
        return [s for s in self.statements if all(n in s for n in needles)]


# --- 1: what the parent schema already guarantees ---------------------------


def test_parent_source_is_not_null(at_parent):
    """Characterization, green from the start.

    It decides how the NULL-source case can be tested at all: the column is
    NOT NULL, so a NULL fixture cannot exist, and weakening the parent schema
    to build one would test a state production cannot reach.
    """
    engine, _cfg, schema = at_parent
    cols = {c["name"]: c for c in inspect(engine).get_columns(
        "environmental_observations", schema=schema)}

    assert cols["source"]["nullable"] is False


# --- 2-3: classification ----------------------------------------------------


def test_upgrade_classifies_measured_production_sources(at_parent):
    from alembic import command

    engine, cfg, schema = at_parent
    _seed(engine, schema, list(MEASURED))
    before = _rows(engine, schema)

    command.upgrade(cfg, TARGET)

    with engine.begin() as c:
        got = dict(c.execute(text(
            f'SELECT source, temporal_kind FROM "{schema}".environmental_observations'
        )).all())
    assert got == MEASURED
    assert _rows(engine, schema) == before, "the migration altered existing fields"


def test_upgrade_classifies_registered_but_unmeasured_openaq(at_parent):
    """openaq holds no production rows. This asserts the frozen policy, not
    history -- a row of it appearing later must classify, not halt."""
    from alembic import command

    engine, cfg, schema = at_parent
    _seed(engine, schema, list(REGISTERED_UNMEASURED))

    command.upgrade(cfg, TARGET)

    with engine.begin() as c:
        assert dict(c.execute(text(
            f'SELECT source, temporal_kind FROM "{schema}".environmental_observations'
        )).all()) == REGISTERED_UNMEASURED


# --- 4-5: the refusal, and where it happens ---------------------------------


def test_unknown_source_fails_before_first_ddl(at_parent):
    from alembic import command

    engine, cfg, schema = at_parent
    _seed(engine, schema, list(MEASURED) + [DELIBERATELY_UNKNOWN])
    rec = Recorder(engine)
    try:
        with pytest.raises(Exception) as exc:
            command.upgrade(cfg, TARGET)
    finally:
        rec.stop()

    assert DELIBERATELY_UNKNOWN in str(exc.value), (
        "the failure does not name the source that caused it"
    )
    assert rec.any("SELECT", "SOURCE"), "no preflight query was issued"
    assert not rec.any("ALTER TABLE"), (
        f"DDL ran before the refusal: {rec.any('ALTER TABLE')}"
    )
    assert not rec.any("UPDATE", "TEMPORAL_KIND"), "classification ran anyway"


def test_preflight_explicitly_handles_null_source():
    """`NOT IN (...)` returns NULL for a NULL source, so such a row would slip
    through as classified. The column is NOT NULL today, so this is asserted
    structurally rather than with a fixture that cannot exist."""
    from pathlib import Path

    src = Path("alembic/versions") / f"{TARGET}_encode_temporal_provenance_on_observations.py"
    body = src.read_text()

    assert "source IS NULL" in body, (
        "the preflight does not test for a NULL source separately"
    )


# --- 6: the contract the upgrade installs -----------------------------------


def test_upgrade_installs_exact_database_contract(at_parent):
    from alembic import command

    engine, cfg, schema = at_parent
    command.upgrade(cfg, TARGET)

    insp = inspect(engine)
    cols = {c["name"]: c for c in insp.get_columns(
        "environmental_observations", schema=schema)}
    assert cols["event_time"]["nullable"] is True
    assert cols["temporal_kind"]["nullable"] is False
    assert cols["period_start"]["nullable"] is True
    assert cols["period_end"]["nullable"] is True

    checks = {c["name"] for c in insp.get_check_constraints(
        "environmental_observations", schema=schema)}
    assert {"ck_environmental_observations_temporal_kind_known",
            "ck_environmental_observations_temporal_kind_state"} <= checks

    with pytest.raises(Exception):
        with engine.begin() as c:
            c.execute(text(
                f'INSERT INTO "{schema}".environmental_observations '
                '(region_id, indicator, value, source, temporal_kind, ingested_at) '
                "VALUES ('RU-MOW','esg',1.0,'openmeteo','observed', now())"))


# --- 7-8: downgrade ---------------------------------------------------------


def test_immediate_downgrade_restores_parent_schema_and_rows(at_parent):
    from alembic import command

    engine, cfg, schema = at_parent
    _seed(engine, schema, list(MEASURED))
    before = _rows(engine, schema)

    command.upgrade(cfg, TARGET)
    command.downgrade(cfg, PARENT)

    insp = inspect(engine)
    names = {c["name"] for c in insp.get_columns(
        "environmental_observations", schema=schema)}
    assert not ({"temporal_kind", "period_start", "period_end"} & names)
    cols = {c["name"]: c for c in insp.get_columns(
        "environmental_observations", schema=schema)}
    assert cols["event_time"]["nullable"] is False
    assert _rows(engine, schema) == before


@pytest.mark.parametrize("kind,extra", [
    ("period", "period_start = now(), period_end = now(), source_revision = 'rev:v1:a'"),
    ("not_applicable", "source_revision = 'rev:v1:a'"),
])
def test_downgrade_refuses_rows_without_event_time(at_parent, kind, extra):
    """There is no honest event_time to invent for these, and inventing one
    restores exactly the falsehood the migration removes. So the downgrade
    refuses -- and must refuse before dropping anything."""
    from alembic import command

    engine, cfg, schema = at_parent
    _seed(engine, schema, ["openmeteo"])
    command.upgrade(cfg, TARGET)
    with engine.begin() as c:
        c.execute(text(
            f'UPDATE "{schema}".environmental_observations SET '
            f"temporal_kind = '{kind}', event_time = NULL, {extra}"))

    rec = Recorder(engine)
    try:
        with pytest.raises(Exception):
            command.downgrade(cfg, PARENT)
    finally:
        rec.stop()

    assert not rec.any("DROP COLUMN"), "the downgrade dropped a column anyway"
    names = {c["name"] for c in inspect(engine).get_columns(
        "environmental_observations", schema=schema)}
    assert {"temporal_kind", "period_start", "period_end"} <= names
