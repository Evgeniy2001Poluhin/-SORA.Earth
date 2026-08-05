"""record how an indicator period was established, or why it was not

`country_indicator_history.as_of_date` is NULL in 90,403 of 93,447 rows. The
ingester discarded the year the World Bank stated alongside every figure (#58).

A single NULL currently carries facts that call for different responses, and
nothing distinguishes them. Worse, the obvious repair -- match the stored value
against the source's series and write the year back -- produces a *fourth* kind
of fact that must not be confused with the first: a period **inferred from a
later response**, not the period the original response stated.

That distinction is the point of this table's vocabulary. The original response
is gone. Whatever a backfill recovers is an inference about it, and calling that
"the source said so" would put an unearned claim on 45,000 rows.

    period_status
      recovered_inferred     exactly one candidate year under a named,
                             versioned rule -- an inference, not a quotation
      ambiguous              several candidate years; the value alone does
                             not identify one
      no_match_current_vintage
                             the source's current data contains no candidate.
                             Revision is one explanation; a changed dataset,
                             identifier, unit or geography are others, and this
                             status does not choose between them
      outside_query_window   the search did not cover the whole possible
                             history, so absence proves nothing
      source_unavailable     no conclusive answer was obtained; retry
      period_not_applicable  a derived value -- a benchmark, a global average --
                             for which the source publishes no period
      unchecked              nothing has looked at this row yet

`unrecoverable` is deliberately absent. It would mean every archive and vintage
had been exhausted, and none has been consulted.

The provenance columns exist so that a verdict can be re-examined rather than
merely trusted: which run made it, under which rule, against which response.
Without them a later correction cannot tell its own earlier work from anyone
else's, and "idempotent" means only "resumable", never "fixable".

Revision ID: e7b3c9d15f04
Revises: c4d1f8a26b93
Create Date: 2026-08-04
"""
from alembic import op
import sqlalchemy as sa

revision = "e7b3c9d15f04"
down_revision = "c4d1f8a26b93"
branch_labels = None
depends_on = None


STATUSES = (
    "recovered_inferred",
    "ambiguous",
    "no_match_current_vintage",
    "outside_query_window",
    "source_unavailable",
    "period_not_applicable",
    "unchecked",
)

COLUMNS = [
    ("period_status", "TEXT"),
    # Which run, under which rule, against which answer. A free-text tag naming
    # only the script cannot distinguish one run from the next, so a bad run
    # cannot be undone without touching the good ones.
    ("period_run_id", "TEXT"),
    ("period_method", "TEXT"),
    ("period_rule_version", "TEXT"),
    ("period_candidates", "INTEGER"),
    ("period_source_vintage", "TEXT"),
    ("period_response_sha256", "TEXT"),
    ("period_resolved_at", "TIMESTAMPTZ"),
]


def upgrade() -> None:
    for name, type_ in COLUMNS:
        op.execute(
            sa.text(
                f"ALTER TABLE country_indicator_history "
                f"ADD COLUMN IF NOT EXISTS {name} {type_}"
            )
        )

    # The vocabulary is closed. Free TEXT admits a typo as a new state, and a
    # status nobody defined reads as data rather than as a mistake.
    op.execute(
        sa.text(
            "ALTER TABLE country_indicator_history "
            "DROP CONSTRAINT IF EXISTS ck_cih_period_status"
        )
    )
    values = ", ".join(f"'{s}'" for s in STATUSES)
    op.execute(
        sa.text(
            f"ALTER TABLE country_indicator_history "
            f"ADD CONSTRAINT ck_cih_period_status CHECK ("
            f"period_status IS NULL OR period_status IN ({values}))"
        )
    )

    # A date is only meaningful for a row that has one, and only these two
    # statuses have one. Without this, `ambiguous` with a date and
    # `recovered_inferred` without one are both storable, and both would be read
    # as facts.
    op.execute(
        sa.text(
            "ALTER TABLE country_indicator_history "
            "DROP CONSTRAINT IF EXISTS ck_cih_period_date_agrees"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE country_indicator_history "
            "ADD CONSTRAINT ck_cih_period_date_agrees CHECK ("
            # A verdict of "recovered" without a date says nothing was
            # recovered, and any other verdict *with* a date says the opposite
            # of what it names. Both are storable without this and both would be
            # read as facts.
            #
            # period_status IS NULL covers rows the ingester dated itself
            # (post-#59): those carry a date and no verdict, which is correct --
            # nothing inferred them.
            "  period_status IS NULL"
            "  OR (period_status = 'recovered_inferred' AND as_of_date IS NOT NULL)"
            "  OR (period_status <> 'recovered_inferred' AND as_of_date IS NULL)"
            ")"
        )
    )

    # "Which rows still have no verdict" is asked on every pass, over ninety
    # thousand rows.
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_cih_period_status "
            "ON country_indicator_history (period_status)"
        )
    )
    # And "what did that run do", which is how a bad run is undone.
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_cih_period_run "
            "ON country_indicator_history (period_run_id)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_cih_period_run"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_cih_period_status"))
    op.execute(
        sa.text(
            "ALTER TABLE country_indicator_history "
            "DROP CONSTRAINT IF EXISTS ck_cih_period_date_agrees"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE country_indicator_history "
            "DROP CONSTRAINT IF EXISTS ck_cih_period_status"
        )
    )
    for name, _ in reversed(COLUMNS):
        op.execute(
            sa.text(
                f"ALTER TABLE country_indicator_history DROP COLUMN IF EXISTS {name}"
            )
        )
