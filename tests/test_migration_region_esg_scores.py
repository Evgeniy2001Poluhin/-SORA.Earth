"""PostgreSQL tests for the region_esg_scores convergence migration.

The migration has to reach the ORM schema from four different starting points,
and must refuse rather than silently succeed when the existing table disagrees
with RegionESGScore. `CREATE TABLE IF NOT EXISTS` would hide exactly that.

These run only against PostgreSQL: the statement under test is a PL/pgSQL DO
block, and the chain is PostgreSQL-only anyway (a1b2c3d4e5f6 uses CREATE OR
REPLACE VIEW, which SQLite rejects).
"""
import os
import re

import pytest
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="the migration under test is PL/pgSQL; needs a PostgreSQL DATABASE_URL",
)

MIGRATION_MODULE = "b7c1e4a92f30_create_region_esg_scores"


def _converge_sql() -> str:
    """Load the DO block straight from the migration, so the test cannot drift."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / f"{MIGRATION_MODULE}.py"
    spec = importlib.util.spec_from_file_location(MIGRATION_MODULE, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CONVERGE_SQL


@pytest.fixture
def db():
    """A throwaway schema per test, so cases cannot contaminate each other."""
    engine = create_engine(DATABASE_URL)
    schema = "mig_test"
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    engine.dispose()

    engine = create_engine(DATABASE_URL, connect_args={"options": f"-csearch_path={schema}"})
    yield engine
    engine.dispose()

    engine = create_engine(DATABASE_URL)
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
    engine.dispose()


def _run(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(_converge_sql()))


def _pk_columns(engine) -> list[str]:
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT a.attname
              FROM pg_constraint con
              JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = ANY (con.conkey)
             WHERE con.conrelid = to_regclass('region_esg_scores') AND con.contype = 'p'
             ORDER BY a.attname
        """)).fetchall()
    return [r[0] for r in rows]


def _has_unique_index(engine, name: str) -> bool:
    with engine.begin() as conn:
        row = conn.execute(text("""
            SELECT pg_get_indexdef(i.indexrelid) FROM pg_index i
              JOIN pg_class ic ON ic.oid = i.indexrelid
             WHERE i.indrelid = to_regclass('region_esg_scores') AND ic.relname = :n
        """), {"n": name}).fetchone()
    return bool(row) and "UNIQUE" in row[0]


LEGACY_COLUMNS = """
    region_code   VARCHAR(10) NOT NULL,
    env_score     DOUBLE PRECISION,
    social_score  DOUBLE PRECISION,
    gov_score     DOUBLE PRECISION,
    total_score   DOUBLE PRECISION,
    confidence    DOUBLE PRECISION,
    sources_count INTEGER,
    signals_used  INTEGER,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
"""


# --------------------------------------------------------------- scenario A


def test_empty_database_creates_the_orm_schema(db):
    _run(db)

    assert _pk_columns(db) == ["id"]
    assert _has_unique_index(db, "ix_region_esg_scores_region_code")

    with db.begin() as conn:
        cols = dict(conn.execute(text("""
            SELECT a.attname, format_type(a.atttypid, NULL) FROM pg_attribute a
             WHERE a.attrelid = to_regclass('region_esg_scores')
               AND a.attnum > 0 AND NOT a.attisdropped
        """)).fetchall())

    assert cols["id"] == "bigint"
    assert cols["region_code"] == "character varying"
    assert cols["updated_at"] == "timestamp with time zone"
    assert cols["sources_count"] == "integer"
    assert cols["env_score"] == "double precision"


# --------------------------------------------------------------- scenario B


def test_matching_schema_is_a_no_op(db):
    _run(db)
    with db.begin() as conn:
        conn.execute(text("INSERT INTO region_esg_scores (region_code) VALUES ('RU-MOW')"))

    _run(db)  # must not raise, must not disturb the row

    assert _pk_columns(db) == ["id"]
    with db.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM region_esg_scores")).scalar() == 1


# --------------------------------------------------------------- scenario C


def test_legacy_region_code_as_primary_key_is_converted(db):
    """The shape this branch previously produced: region_code as the PK, no id."""
    with db.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE region_esg_scores (
                {LEGACY_COLUMNS},
                CONSTRAINT pk_region_esg_scores PRIMARY KEY (region_code)
            )
        """))
        conn.execute(text("INSERT INTO region_esg_scores (region_code) VALUES ('RU-SPE')"))

    _run(db)

    assert _pk_columns(db) == ["id"]
    assert _has_unique_index(db, "ix_region_esg_scores_region_code")
    with db.begin() as conn:
        row = conn.execute(text("SELECT region_code, id FROM region_esg_scores")).fetchone()
    assert row[0] == "RU-SPE"
    assert row[1] is not None, "the existing row must receive a generated id"


def test_production_like_id_unique_but_not_primary_key_is_converted(db):
    """What cb5a035aed2f leaves behind: id exists and is unique, PK elsewhere."""
    with db.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE region_esg_scores (
                id BIGINT GENERATED ALWAYS AS IDENTITY,
                {LEGACY_COLUMNS},
                CONSTRAINT pk_region_esg_scores PRIMARY KEY (region_code)
            )
        """))
        conn.execute(text("CREATE UNIQUE INDEX ix_region_esg_scores_id ON region_esg_scores(id)"))
        conn.execute(text("INSERT INTO region_esg_scores (region_code) VALUES ('RU-LEN')"))

    _run(db)

    assert _pk_columns(db) == ["id"]
    with db.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM region_esg_scores")).scalar() == 1


# --------------------------------------------------------------- scenario D


def test_duplicate_ids_fail_fast(db):
    with db.begin() as conn:
        conn.execute(text(f"CREATE TABLE region_esg_scores (id BIGINT, {LEGACY_COLUMNS})"))
        conn.execute(text("""
            INSERT INTO region_esg_scores (id, region_code) VALUES (1, 'A'), (1, 'B')
        """))

    with pytest.raises(Exception, match="duplicated id"):
        _run(db)


def test_null_ids_fail_fast(db):
    with db.begin() as conn:
        conn.execute(text(f"CREATE TABLE region_esg_scores (id BIGINT, {LEGACY_COLUMNS})"))
        conn.execute(text("INSERT INTO region_esg_scores (id, region_code) VALUES (NULL, 'A')"))

    with pytest.raises(Exception, match="NULL id"):
        _run(db)


def test_incompatible_column_type_fails_fast(db):
    with db.begin() as conn:
        conn.execute(text("""
            CREATE TABLE region_esg_scores (
                region_code   VARCHAR(10) NOT NULL,
                env_score     TEXT,
                social_score  DOUBLE PRECISION,
                gov_score     DOUBLE PRECISION,
                total_score   DOUBLE PRECISION,
                confidence    DOUBLE PRECISION,
                sources_count INTEGER,
                signals_used  INTEGER,
                updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))

    with pytest.raises(Exception, match="incompatible column type"):
        _run(db)


def test_missing_column_fails_fast(db):
    with db.begin() as conn:
        conn.execute(text("""
            CREATE TABLE region_esg_scores (
                region_code VARCHAR(10) NOT NULL,
                env_score   DOUBLE PRECISION
            )
        """))

    with pytest.raises(Exception, match="missing column"):
        _run(db)


def test_incompatible_id_type_fails_fast(db):
    with db.begin() as conn:
        conn.execute(text(f"CREATE TABLE region_esg_scores (id TEXT, {LEGACY_COLUMNS})"))

    with pytest.raises(Exception, match="id has type"):
        _run(db)


def test_foreign_key_referencing_the_table_blocks_the_pk_move(db):
    """Moving the primary key would break the reference, so refuse."""
    with db.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE region_esg_scores (
                {LEGACY_COLUMNS},
                CONSTRAINT pk_region_esg_scores PRIMARY KEY (region_code)
            )
        """))
        conn.execute(text("""
            CREATE TABLE dependent_thing (
                region_code VARCHAR(10) REFERENCES region_esg_scores(region_code)
            )
        """))

    with pytest.raises(Exception, match="foreign key"):
        _run(db)


# --------------------------------------------------------------- dependencies


def test_dependent_view_survives_convergence(db):
    """regional_esg_snapshot reads this table; converging must not drop it."""
    with db.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE region_esg_scores (
                {LEGACY_COLUMNS},
                CONSTRAINT pk_region_esg_scores PRIMARY KEY (region_code)
            )
        """))
        conn.execute(text("""
            CREATE VIEW regional_esg_snapshot AS
            SELECT region_code, env_score AS e_score FROM region_esg_scores
        """))

    _run(db)

    with db.begin() as conn:
        # Scoped to the throwaway schema: a same-named view may exist elsewhere.
        exists = conn.execute(text("""
            SELECT count(*) FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE c.relname = 'regional_esg_snapshot'
               AND c.relkind = 'v'
               AND n.nspname = current_schema()
        """)).scalar()
    assert exists == 1
    assert _pk_columns(db) == ["id"]


def test_migration_is_idempotent_across_repeated_runs(db):
    _run(db)
    _run(db)
    _run(db)

    assert _pk_columns(db) == ["id"]
    with db.begin() as conn:
        count = conn.execute(text("""
            SELECT count(*) FROM pg_index i
              JOIN pg_class ic ON ic.oid = i.indexrelid
             WHERE i.indrelid = to_regclass('region_esg_scores')
               AND ic.relname = 'ix_region_esg_scores_region_code'
        """)).scalar()
    assert count == 1


def test_do_block_is_emitted_verbatim_offline():
    """The offline script must carry the same statement, not a stub."""
    sql = _converge_sql()
    assert "DO $migration$" in sql
    assert "RAISE EXCEPTION" in sql
    assert not re.search(r"\bsa\.inspect\b", sql), "no Inspector may be involved"
