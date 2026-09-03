"""Build a throwaway database in production's shape, for restore drills.

The shape comes from `alembic upgrade head`, because that is how production's
schema is built. Nothing here describes the schema; describing it is what went
wrong before.

Everything here is synthetic. This script never reads from, connects to, or
copies production.

    DATABASE_URL=postgresql://user:pass@host:port/db python scripts/drill_seed_prod_shape.py

## What this replaced, and why

The previous version hardcoded a `LEGACY_TABLE_SQL` that recreated
`region_esg_scores` with `region_code` as the primary key and without
`stale_since` / `stale_reason`, on the stated grounds that production predated
the migration chain — an older `create_all()` had made the table, and the ORM had
since moved on. It also pinned `alembic_version` to revision `0b0ff6d1594e`.

Both statements were true of the server that was deleted for non-payment on
2026-08-16. The replacement was built from an empty database by running the
migrations, so it has no such divergence. Measured on it, 2026-09-03:

    columns  id, region_code, env_score, social_score, gov_score, total_score,
             confidence, sources_count, signals_used, updated_at,
             stale_since, stale_reason
    pk       id
    revision d2a7f4b81c65

The drill could not pass against the old shape: `drill_smoke.py` reads through
the ORM, which selects `stale_since`, and the seeded table had no such column.

    sqlalchemy.exc.ProgrammingError: (psycopg.errors.UndefinedColumn)
    column region_esg_scores.stale_since does not exist

That is the failure mode of a fixture that states a fact instead of deriving it:
it stays green while its subject is unchanged, and it goes wrong silently at the
moment the subject moves. Running the migrations cannot drift from the
migrations.
"""
import os
import random
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import sqlalchemy as sa

ROW_COUNT = 85
RANDOM_SEED = 20260728  # fixed so two drills seed byte-identical data


def synthetic_rows(count=ROW_COUNT, seed=RANDOM_SEED):
    """Deterministic stand-ins for the regional scores."""
    rng = random.Random(seed)
    for n in range(1, count + 1):
        env, soc, gov = (round(rng.uniform(20, 95), 2) for _ in range(3))
        yield {
            "region_code": f"RU-{n:03d}",
            "env_score": env,
            "social_score": soc,
            "gov_score": gov,
            "total_score": round((env + soc + gov) / 3, 2),
            "confidence": round(rng.uniform(0.4, 0.99), 3),
            "sources_count": rng.randint(1, 9),
            "signals_used": rng.randint(1, 40),
        }


def migrate(url):
    """Take the database to head, the same way the deployment does.

    Run as a subprocess rather than through alembic's Python API: the
    deployment runs `alembic upgrade head` from `docker compose run migrate`,
    and a drill that reaches the same schema by a different route is proving
    something about the route rather than about the schema.
    """
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    env = {**os.environ, "DATABASE_URL": url}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=root, env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        sys.exit("alembic upgrade head failed; the drill has no schema to seed")
    return result.stdout.strip().splitlines()[-1:] or [""]


def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is required")
    if not url.startswith("postgresql"):
        sys.exit(f"refusing to seed a non-PostgreSQL target: {url}")

    migrate(url)

    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(sa.text("SET client_min_messages TO WARNING"))
        # Empty first: the drill compares counts before and after a restore, so
        # a rerun against a database that already has rows would seed 170 and
        # then compare 170 against 170 -- true, and about nothing.
        conn.execute(sa.text("DELETE FROM region_esg_scores"))
        conn.execute(
            sa.text(
                "INSERT INTO region_esg_scores "
                "(region_code, env_score, social_score, gov_score, total_score,"
                " confidence, sources_count, signals_used, updated_at) VALUES "
                "(:region_code, :env_score, :social_score, :gov_score, :total_score,"
                " :confidence, :sources_count, :signals_used,"
                " TIMESTAMPTZ '2026-07-01 00:00:00+00')"
            ),
            list(synthetic_rows()),
        )

    with engine.connect() as conn:
        seeded = conn.execute(sa.text("SELECT count(*) FROM region_esg_scores")).scalar()
        revision = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
        via_view = conn.execute(sa.text("SELECT count(*) FROM regional_esg_snapshot")).scalar()
        # The column the old fixture omitted. Asserted rather than assumed,
        # because its absence is what made the drill unpassable and the failure
        # appeared three steps later, inside the ORM.
        has_stale = conn.execute(sa.text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'region_esg_scores' "
            "AND column_name IN ('stale_since', 'stale_reason')")).scalar()

    print(f"seeded rows           : {seeded}")
    print(f"alembic_version       : {revision}")
    print(f"rows through the view : {via_view}")
    print(f"stale_* columns       : {has_stale} of 2")

    if seeded != ROW_COUNT or via_view != ROW_COUNT:
        sys.exit(f"expected {ROW_COUNT} rows both directly and through the view")
    if has_stale != 2:
        sys.exit("region_esg_scores is missing stale_since/stale_reason; the "
                 "migrations did not produce the schema the ORM expects")


if __name__ == "__main__":
    main()
