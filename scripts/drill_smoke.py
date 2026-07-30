"""Check that the application can read a restored database.

Row counts prove the data survived; this proves the application can still get
at it. The two are not the same thing — a restore can bring back every row and
still leave the ORM unable to map the table, or the view unreadable.

    DATABASE_URL=postgresql://... python scripts/drill_smoke.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import sqlalchemy as sa

EXPECTED_ROWS = 85


def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is required")

    from app.database import RegionESGScore

    engine = sa.create_engine(url)
    failures = []

    with sa.orm.Session(engine) as session:
        # Reads through the ORM, which is how the application sees this table.
        # Note the ORM declares id as the primary key while the restored table
        # keys on region_code; the drill records that divergence rather than
        # hiding it, because the convergence migration is what resolves it.
        via_orm = session.query(RegionESGScore).count()
        if via_orm != EXPECTED_ROWS:
            failures.append(f"ORM read {via_orm} rows, expected {EXPECTED_ROWS}")

        sample = session.query(RegionESGScore).order_by(RegionESGScore.region_code).first()
        if sample is None or sample.total_score is None:
            failures.append("ORM returned no usable row")

    with engine.connect() as conn:
        # The map_russia route reads the view, not the table.
        view_rows = conn.execute(
            sa.text("SELECT count(*) FROM regional_esg_snapshot")
        ).scalar()
        if view_rows != EXPECTED_ROWS:
            failures.append(f"view returned {view_rows} rows, expected {EXPECTED_ROWS}")

        columns = conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'regional_esg_snapshot' ORDER BY column_name"
            )
        ).scalars().all()
        for required in ("e_score", "s_score", "g_score", "score", "confidence", "computed_at"):
            if required not in columns:
                failures.append(f"view is missing column {required}")

    if failures:
        for line in failures:
            print(f"    {line}")
        sys.exit(1)
    print(f"    ORM rows={EXPECTED_ROWS}, view rows={EXPECTED_ROWS}, view columns intact")


if __name__ == "__main__":
    main()
