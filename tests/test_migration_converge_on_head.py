"""Tests for e3f8a7c15d92, the convergence catch-up revision.

b7c1e4a92f30 sits behind the point existing deployments have already reached,
so Alembic marks it applied without running it. e3f8a7c15d92 sits after the
head and performs the same convergence on those databases.

Two properties have to hold, and they are tested separately:

  * the two revisions must carry identical SQL, or a converged deployment and
    a fresh install would land on different schemas — which is the defect the
    chain exists to close. That check is pure Python and runs everywhere.
  * the convergence must actually reach the canonical schema from the shape
    production carries. That needs PL/pgSQL, so it runs only on PostgreSQL.
"""
import importlib.util
import os
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
CREATE_REVISION = "b7c1e4a92f30_create_region_esg_scores"
CATCHUP_REVISION = "e3f8a7c15d92_converge_region_esg_scores_on_head"

DATABASE_URL = os.getenv("DATABASE_URL", "")
requires_postgres = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="the statement under test is PL/pgSQL; needs a PostgreSQL DATABASE_URL",
)


def _load(module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, VERSIONS / f"{module_name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _converge_sql(module_name: str) -> str:
    return _load(module_name).CONVERGE_SQL


# --------------------------------------------------------------- drift guard


def test_catchup_sql_is_identical_to_the_create_revision():
    """The whole point of the catch-up is to reach the same schema.

    If these drift, deployments that ran b7c1e4a92f30 and deployments that ran
    e3f8a7c15d92 end up on different schemas, silently.
    """
    assert _converge_sql(CATCHUP_REVISION) == _converge_sql(CREATE_REVISION)


def test_catchup_follows_the_revision_production_sits_on():
    """Production records 0b0ff6d1594e, so the catch-up has to come after it.

    Anywhere earlier and it would be treated as already applied, which is the
    exact reason b7c1e4a92f30 alone is not enough.
    """
    assert _load(CATCHUP_REVISION).down_revision == "0b0ff6d1594e"


def test_chain_has_a_single_head():
    """Two heads make `alembic upgrade head` refuse to run at all."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1]
    script = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))
    assert len(script.get_heads()) == 1, f"expected one head, got {script.get_heads()}"


def test_downgrade_does_not_drop_or_alter_anything():
    """A destructive downgrade is the failure mode this revision must avoid.

    Downgrading walks through this revision on the way past it, on a database
    holding rows that predate the migration chain entirely.
    """
    source = (VERSIONS / f"{CATCHUP_REVISION}.py").read_text()
    body = source.split("def downgrade()", 1)[1]
    statement = body.split('"""', 2)[2]
    for forbidden in ("DROP TABLE", "DROP VIEW", "DROP INDEX", "ALTER TABLE", "DELETE", "TRUNCATE"):
        assert forbidden not in statement.upper(), f"downgrade must not contain {forbidden}"


# ------------------------------------------------------------ PostgreSQL path

LEGACY_PRODUCTION_DDL = """
CREATE TABLE region_esg_scores (
    region_code   text PRIMARY KEY,
    env_score     real,
    social_score  real,
    gov_score     real,
    total_score   real,
    confidence    real,
    updated_at    timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE region_esg_scores ADD COLUMN id BIGINT GENERATED ALWAYS AS IDENTITY;
ALTER TABLE region_esg_scores ADD COLUMN sources_count INTEGER;
ALTER TABLE region_esg_scores ADD COLUMN signals_used INTEGER;
CREATE UNIQUE INDEX ix_region_esg_scores_id ON region_esg_scores(id);
CREATE VIEW regional_esg_snapshot AS
SELECT region_code,
       env_score    AS e_score,
       social_score AS s_score,
       gov_score    AS g_score,
       total_score  AS score,
       confidence,
       ARRAY[]::text[] AS sources_used,
       updated_at   AS computed_at
FROM region_esg_scores;
"""

CANONICAL_DDL = """
CREATE TABLE region_esg_scores (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    region_code   VARCHAR(10) NOT NULL,
    env_score     DOUBLE PRECISION,
    social_score  DOUBLE PRECISION,
    gov_score     DOUBLE PRECISION,
    total_score   DOUBLE PRECISION,
    confidence    DOUBLE PRECISION,
    sources_count INTEGER,
    signals_used  INTEGER,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ix_region_esg_scores_region_code ON region_esg_scores (region_code);
"""


@pytest.fixture
def db():
    """A throwaway schema per test, so cases cannot contaminate each other."""
    engine = create_engine(DATABASE_URL)
    schema = "catchup_test"
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


def _exec(engine, sql: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(sql))


def _converge(engine) -> None:
    _exec(engine, _converge_sql(CATCHUP_REVISION))


def _seed(engine, count: int = 85) -> None:
    _exec(engine, f"""
        INSERT INTO region_esg_scores
               (region_code, env_score, social_score, gov_score, total_score, confidence)
        SELECT 'RU-' || lpad(g::text, 3, '0'), 50+g*0.1, 60+g*0.1, 55+g*0.1, 58+g*0.1, 0.8
          FROM generate_series(1, {count}) g
    """)


def _columns(engine) -> dict[str, str]:
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT a.attname, format_type(a.atttypid, a.atttypmod)
              FROM pg_attribute a
             WHERE a.attrelid = to_regclass('region_esg_scores')
               AND a.attnum > 0 AND NOT a.attisdropped
        """)).fetchall()
    return {name: typ for name, typ in rows}


def _pk(engine) -> list[str]:
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT a.attname
              FROM pg_constraint con
              JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = ANY (con.conkey)
             WHERE con.conrelid = to_regclass('region_esg_scores') AND con.contype = 'p'
             ORDER BY a.attname
        """)).fetchall()
    return [r[0] for r in rows]


def _scalar(engine, sql: str):
    with engine.begin() as conn:
        return conn.execute(text(sql)).scalar()


@requires_postgres
def test_converges_the_production_shape(db):
    """The shape production actually carries: region_code PK, text, real scores."""
    _exec(db, LEGACY_PRODUCTION_DDL)
    _seed(db)

    _converge(db)

    columns = _columns(db)
    assert columns["region_code"] == "character varying(10)"
    assert columns["env_score"] == "double precision"
    assert columns["total_score"] == "double precision"
    assert _pk(db) == ["id"]
    assert _scalar(db, "SELECT count(*) FROM region_esg_scores") == 85


@requires_postgres
def test_rows_survive_the_conversion_unchanged(db):
    _exec(db, LEGACY_PRODUCTION_DDL)
    _seed(db)
    before = _scalar(db, "SELECT round(sum(total_score)::numeric, 2) FROM region_esg_scores")

    _converge(db)

    after = _scalar(db, "SELECT round(sum(total_score)::numeric, 2) FROM region_esg_scores")
    assert after == before
    assert _scalar(db, "SELECT min(region_code) FROM region_esg_scores") == "RU-001"
    assert _scalar(db, "SELECT max(region_code) FROM region_esg_scores") == "RU-085"


@requires_postgres
def test_view_survives_and_still_reads(db):
    """regional_esg_snapshot backs the map_russia API and blocks ALTER COLUMN TYPE."""
    _exec(db, LEGACY_PRODUCTION_DDL)
    _seed(db)

    _converge(db)

    assert _scalar(db, "SELECT count(*) FROM regional_esg_snapshot") == 85


@requires_postgres
def test_identity_sequence_does_not_collide_with_existing_rows(db):
    """Rows predate the identity column, so the sequence has to be moved past them."""
    _exec(db, LEGACY_PRODUCTION_DDL)
    _seed(db)

    _converge(db)

    _exec(db, "INSERT INTO region_esg_scores (region_code, total_score) VALUES ('RU-999', 42.0)")
    assert _scalar(db, "SELECT id FROM region_esg_scores WHERE region_code = 'RU-999'") == 86


@requires_postgres
def test_is_a_no_op_on_an_already_canonical_table(db):
    """The fresh-install path: b7c1e4a92f30 already produced this schema."""
    _exec(db, CANONICAL_DDL)
    before = _columns(db)

    _converge(db)

    assert _columns(db) == before
    assert _pk(db) == ["id"]


@requires_postgres
def test_running_it_twice_changes_nothing(db):
    _exec(db, LEGACY_PRODUCTION_DDL)
    _seed(db)
    _converge(db)
    once = _columns(db)

    _converge(db)

    assert _columns(db) == once
    assert _pk(db) == ["id"]
    assert _scalar(db, "SELECT count(*) FROM region_esg_scores") == 85


@requires_postgres
def test_converged_schema_matches_a_fresh_install(db):
    """Converged production and a fresh install must be indistinguishable.

    Physical column order is not compared: it necessarily differs, because id
    arrives through ADD COLUMN on an existing table, and nothing addresses
    these columns positionally.
    """
    _exec(db, LEGACY_PRODUCTION_DDL)
    _seed(db)
    _converge(db)
    converged, converged_pk = _columns(db), _pk(db)

    _exec(db, "DROP VIEW regional_esg_snapshot; DROP TABLE region_esg_scores")
    _exec(db, CANONICAL_DDL)

    assert converged == _columns(db)
    assert converged_pk == _pk(db)


@requires_postgres
def test_refuses_rather_than_truncating_an_over_long_region_code(db):
    """text -> VARCHAR(10) is narrowing; losing data silently is the worst outcome."""
    _exec(db, LEGACY_PRODUCTION_DDL)
    _exec(db, "INSERT INTO region_esg_scores (region_code) VALUES ('THIS-CODE-IS-FAR-TOO-LONG')")

    with pytest.raises(Exception, match="would truncate data"):
        _converge(db)

    assert _scalar(db, "SELECT count(*) FROM region_esg_scores") == 1


@requires_postgres
def test_refuses_an_unrecognised_column_type(db):
    """Anything outside the canonical and known-legacy shapes must raise, not guess."""
    _exec(db, LEGACY_PRODUCTION_DDL)
    _exec(db, "DROP VIEW regional_esg_snapshot")
    _exec(db, "ALTER TABLE region_esg_scores ALTER COLUMN env_score TYPE numeric(6,2)")

    with pytest.raises(Exception, match="neither canonical nor a recognised legacy shape"):
        _converge(db)


# ------------------------------------------- metadata preservation and lineage


def test_the_historical_bootstrap_does_not_run_on_an_existing_deployment():
    """The premise of this revision, asserted rather than assumed.

    b7c1e4a92f30 is an ancestor of the revision production records, so Alembic
    treats it as applied and never executes it. If that ever stopped being
    true, this revision would be redundant — and if the reverse happened and
    the catch-up fell behind the recorded version, it would be inert. The path
    from 0b0ff6d1594e to head must therefore be exactly this one revision.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1]
    script = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))

    path = [rev.revision for rev in script.iterate_revisions("head", "0b0ff6d1594e")]

    assert path == ["e3f8a7c15d92"], (
        f"expected only the catch-up between 0b0ff6d1594e and head, got {path}"
    )
    assert "b7c1e4a92f30" not in path, "the historical bootstrap must not be in this path"


@requires_postgres
def test_view_owner_grants_and_comment_survive_the_conversion(db):
    """The view is dropped and recreated, so everything it carried must return.

    CREATE VIEW ... AS <definition> restores none of this on its own: the owner
    reverts to whoever runs the migration, the ACL resets to owner-only, and the
    comment is gone. On production that would silently revoke access for
    whatever role reads regional_esg_snapshot.
    """
    _exec(db, "DROP ROLE IF EXISTS sora_view_reader")
    _exec(db, "CREATE ROLE sora_view_reader")
    try:
        _exec(db, LEGACY_PRODUCTION_DDL)
        _seed(db)
        _exec(db, "COMMENT ON VIEW regional_esg_snapshot IS 'map_russia API source'")
        _exec(db, "GRANT SELECT ON regional_esg_snapshot TO sora_view_reader")
        _exec(db, "GRANT SELECT ON regional_esg_snapshot TO PUBLIC")

        owner_before = _scalar(db, """
            SELECT pg_get_userbyid(relowner) FROM pg_class
             WHERE relname = 'regional_esg_snapshot' AND relkind = 'v'
        """)

        _converge(db)

        owner_after = _scalar(db, """
            SELECT pg_get_userbyid(relowner) FROM pg_class
             WHERE relname = 'regional_esg_snapshot' AND relkind = 'v'
        """)
        assert owner_after == owner_before, "view owner changed"

        comment = _scalar(db, """
            SELECT obj_description(c.oid, 'pg_class') FROM pg_class c
             WHERE c.relname = 'regional_esg_snapshot' AND c.relkind = 'v'
        """)
        assert comment == "map_russia API source", f"view comment lost: {comment!r}"

        with db.begin() as conn:
            grants = {
                (row[0], row[1])
                for row in conn.execute(text("""
                    SELECT CASE WHEN acl.grantee = 0 THEN 'PUBLIC'
                                ELSE pg_get_userbyid(acl.grantee) END,
                           acl.privilege_type
                      FROM (SELECT (aclexplode(c.relacl)).*
                              FROM pg_class c
                             WHERE c.relname = 'regional_esg_snapshot'
                               AND c.relkind = 'v') acl
                """)).fetchall()
            }

        assert ("sora_view_reader", "SELECT") in grants, f"named grant lost: {grants}"
        assert ("PUBLIC", "SELECT") in grants, f"PUBLIC grant lost: {grants}"
    finally:
        _exec(db, "DROP OWNED BY sora_view_reader CASCADE")
        _exec(db, "DROP ROLE IF EXISTS sora_view_reader")


@requires_postgres
def test_next_insert_takes_max_id_plus_one(db):
    """Rows predate the identity column, so the sequence starts behind them."""
    _exec(db, LEGACY_PRODUCTION_DDL)
    _seed(db)
    max_before = _scalar(db, "SELECT max(id) FROM region_esg_scores")

    _converge(db)

    _exec(db, "INSERT INTO region_esg_scores (region_code, total_score) VALUES ('RU-901', 1.0)")
    assert _scalar(db, "SELECT id FROM region_esg_scores WHERE region_code = 'RU-901'") == max_before + 1


@requires_postgres
def test_a_second_convergence_is_a_no_op_down_to_the_view(db):
    """Idempotency has to hold for the view too, not just the table.

    The conversion branch is skipped once the types are canonical, so the view
    must not be dropped and recreated a second time — that would reset its
    owner and ACL on a database that is already correct.
    """
    _exec(db, "DROP ROLE IF EXISTS sora_view_reader2")
    _exec(db, "CREATE ROLE sora_view_reader2")
    try:
        _exec(db, LEGACY_PRODUCTION_DDL)
        _seed(db)
        _converge(db)
        _exec(db, "GRANT SELECT ON regional_esg_snapshot TO sora_view_reader2")

        columns_once, pk_once = _columns(db), _pk(db)
        view_oid_once = _scalar(db, "SELECT to_regclass('regional_esg_snapshot')::oid")

        _converge(db)

        assert _columns(db) == columns_once
        assert _pk(db) == pk_once
        assert _scalar(db, "SELECT to_regclass('regional_esg_snapshot')::oid") == view_oid_once, \
            "the view was recreated on a second run; it should have been left alone"
        assert _scalar(db, "SELECT count(*) FROM region_esg_scores") == 85
    finally:
        _exec(db, "DROP OWNED BY sora_view_reader2 CASCADE")
        _exec(db, "DROP ROLE IF EXISTS sora_view_reader2")
