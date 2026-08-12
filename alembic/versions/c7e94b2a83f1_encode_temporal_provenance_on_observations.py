"""encode temporal provenance on environmental_observations

`event_time` meant four things at once, and persistence filled it with
`datetime.now()` whenever a source had none -- so 85 constants and an offline
2024 snapshot were both recorded as measured today (#121). This adds the
columns that let a row say what kind of time it carries, and classifies what is
already stored.

The mapping is frozen here rather than imported from
`app/ingesters/source_register.py`. A live import would let a registry edit six
months from now change what an already-released migration did; a released
revision has to be a snapshot of the knowledge that existed when it ran.

Measured against production on 2026-08-12, one read-only aggregate:

    openaq                 observed                   0
    openmeteo              observed              61 090
    openmeteo_air_quality  observed              21 918
    rosstat                legacy_ingestion_time 10 200
    sber_veb_baseline      legacy_ingestion_time  2 040

`openaq` is classified although it has no rows: its semantics are known, and a
row appearing later should be classified rather than halt the migration.
`world_bank` is deliberately absent -- it exists in the code for a different
table, and a row of it here must stop this migration rather than receive a
plausible guess. There is no `ELSE`: an unknown source is a refusal.

The counts are a measurement, not an expectation. The migration compares its
own before and after counts and works for a table of any size holding these
sources.

Parent is d3f0a71c9b48, the single head and the revision production is on.
f7a8b9c0d1e2 created the table and looks like the semantic parent, but chaining
there would fork a graph that 013bbc52a33f already merged.

Revision ID: c7e94b2a83f1
Revises: d3f0a71c9b48
Create Date: 2026-08-12
"""
from alembic import op
import sqlalchemy as sa


revision = "c7e94b2a83f1"
down_revision = "d3f0a71c9b48"
branch_labels = None
depends_on = None


TABLE = "environmental_observations"

#: Frozen at release. See the module docstring for why this is not imported.
SOURCE_TEMPORAL_KIND = {
    "openaq": "observed",                     # measured, 0 rows in production
    "openmeteo": "observed",                  # modelled
    "openmeteo_air_quality": "observed",      # modelled
    "rosstat": "legacy_ingestion_time",       # administrative snapshot
    "sber_veb_baseline": "legacy_ingestion_time",   # static baseline
}

CK_KNOWN = "ck_environmental_observations_temporal_kind_known"
CK_STATE = "ck_environmental_observations_temporal_kind_state"

#: Ordered digest of the fields that must survive untouched. Also catches a
#: trigger rewriting them during the classification UPDATE, which no column
#: comparison would notice.
_DIGEST = f"""
SELECT md5(coalesce(string_agg(
    md5(jsonb_build_array(id, source, source_record_id, event_time)::text),
    '' ORDER BY id), ''))
FROM {TABLE}
"""


def _lock(conn):
    """Block writers, with a bound on how long we wait for them.

    SHARE ROW EXCLUSIVE stops INSERT/UPDATE/DELETE while leaving ordinary reads
    working. The ALTERs below take a stronger lock of their own; taking one
    here closes the window between the preflight and the classification, in
    which a row of an unknown source could otherwise appear and be missed.

    `lock_timeout` so a deployment fails fast and visibly instead of waiting
    behind a long transaction with the table locked against writes.
    """
    conn.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    conn.execute(sa.text(f"LOCK TABLE {TABLE} IN SHARE ROW EXCLUSIVE MODE"))


def upgrade() -> None:
    conn = op.get_bind()
    _lock(conn)

    # --- preflight, before any DDL -----------------------------------------
    #
    # `source IS NULL` is tested separately: `source NOT IN (...)` evaluates to
    # NULL for a NULL source, so such a row would not appear in the result and
    # would slip through as classified. The column is NOT NULL today; the check
    # costs nothing and survives that changing.
    names = ", ".join(f"'{s}'" for s in sorted(SOURCE_TEMPORAL_KIND))
    unknown = conn.execute(sa.text(
        f"SELECT source, count(*) FROM {TABLE} "
        f"WHERE source IS NULL OR source NOT IN ({names}) "
        f"GROUP BY source ORDER BY source NULLS FIRST"
    )).fetchall()
    if unknown:
        listed = ", ".join(f"{r[0]!r}: {r[1]} rows" for r in unknown)
        raise RuntimeError(
            f"refusing to migrate {TABLE}: {len(unknown)} source(s) are not in "
            f"this revision's frozen mapping -- {listed}. Classify them "
            f"deliberately (registry, this mapping and a test fixture) rather "
            f"than letting a default assign one."
        )

    before_count = conn.execute(sa.text(f"SELECT count(*) FROM {TABLE}")).scalar()
    before_digest = conn.execute(sa.text(_DIGEST)).scalar()

    # --- schema -------------------------------------------------------------
    op.add_column(TABLE, sa.Column("temporal_kind", sa.String(32), nullable=True))
    op.add_column(TABLE, sa.Column("period_start", sa.DateTime(timezone=True), nullable=True))
    op.add_column(TABLE, sa.Column("period_end", sa.DateTime(timezone=True), nullable=True))
    op.alter_column(TABLE, "event_time", existing_type=sa.DateTime(timezone=True),
                    nullable=True)

    # --- classification, one source at a time, no ELSE ----------------------
    for source, kind in sorted(SOURCE_TEMPORAL_KIND.items()):
        conn.execute(sa.text(
            f"UPDATE {TABLE} SET temporal_kind = :k WHERE source = :s"),
            {"k": kind, "s": source})

    left = conn.execute(sa.text(
        f"SELECT count(*) FROM {TABLE} WHERE temporal_kind IS NULL")).scalar()
    if left:
        raise RuntimeError(
            f"{left} row(s) were left unclassified; the preflight passed, so "
            f"something wrote during the migration despite the table lock"
        )

    after_count = conn.execute(sa.text(f"SELECT count(*) FROM {TABLE}")).scalar()
    if after_count != before_count:
        raise RuntimeError(
            f"row count changed during the migration: {before_count} -> {after_count}"
        )
    after_digest = conn.execute(sa.text(_DIGEST)).scalar()
    if after_digest != before_digest:
        raise RuntimeError(
            "id, source, source_record_id or event_time changed during the "
            "migration; only temporal_kind was meant to move"
        )

    op.alter_column(TABLE, "temporal_kind", existing_type=sa.String(32),
                    nullable=False)
    op.create_index(f"ix_{TABLE}_temporal_kind", TABLE, ["temporal_kind"])

    # The same two constraints the model declares, by the same names, so
    # metadata and the database describe one contract rather than two.
    op.create_check_constraint(
        CK_KNOWN, TABLE,
        "temporal_kind IN ('observed', 'period', 'not_applicable', "
        "'legacy_ingestion_time')",
    )
    op.create_check_constraint(
        CK_STATE, TABLE,
        "(temporal_kind IN ('observed', 'legacy_ingestion_time')"
        "  AND event_time IS NOT NULL"
        "  AND period_start IS NULL AND period_end IS NULL)"
        " OR (temporal_kind = 'period'"
        "  AND event_time IS NULL"
        "  AND period_start IS NOT NULL AND period_end IS NOT NULL"
        "  AND period_start <= period_end"
        "  AND source_revision IS NOT NULL)"
        " OR (temporal_kind = 'not_applicable'"
        "  AND event_time IS NULL"
        "  AND period_start IS NULL AND period_end IS NULL"
        "  AND source_revision IS NOT NULL)",
    )


def downgrade() -> None:
    """Fail-closed. There is no honest `event_time` to invent.

    A `period` or `not_applicable` row has no observation date by definition.
    Filling one in to satisfy the old NOT NULL would restore exactly the
    falsehood this migration removes -- so the downgrade refuses instead, and
    refuses before dropping anything, so a failed attempt leaves the schema
    whole.
    """
    conn = op.get_bind()
    _lock(conn)

    stranded = conn.execute(sa.text(
        f"SELECT count(*) FROM {TABLE} WHERE event_time IS NULL")).scalar()
    if stranded:
        raise RuntimeError(
            f"refusing to downgrade: {stranded} row(s) have no event_time. "
            f"Restoring NOT NULL would require inventing one, which is the "
            f"defect this revision removes. Remove or re-source those rows "
            f"deliberately first."
        )

    op.drop_constraint(CK_STATE, TABLE, type_="check")
    op.drop_constraint(CK_KNOWN, TABLE, type_="check")
    op.drop_index(f"ix_{TABLE}_temporal_kind", table_name=TABLE)
    op.alter_column(TABLE, "event_time", existing_type=sa.DateTime(timezone=True),
                    nullable=False)
    op.drop_column(TABLE, "period_end")
    op.drop_column(TABLE, "period_start")
    op.drop_column(TABLE, "temporal_kind")
