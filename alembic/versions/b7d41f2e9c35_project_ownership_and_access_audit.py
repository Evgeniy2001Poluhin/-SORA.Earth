"""project ownership and the administrator access audit

`evaluations` and `predictions_log` gain an owner and a scope, and two tables
record every administrator request that reads or removes project rows (see the
note after app/database.py:PredictionLog, and ProjectAccessEvent).

Every row that exists when this runs becomes `public` with no owner. That is
not a guess about who wrote it: it keeps the rows exactly where they were, in
the shared corpus. Nothing here matches a row to an account by name, time,
address or input -- a match like that would be a reconstruction presented as a
fact. A database that already holds a private experiment with real people has
to be sorted out by hand before this runs; a migration cannot know.

The order matters. The columns are added nullable, the existing rows are filled,
and only then do the default (`pilot`), NOT NULL, the foreign keys and the CHECK
arrive -- so the constraint is checked against the backfilled rows, and a row
written afterwards without an owner fails instead of turning public.

One transaction, and a bounded wait for locks: on a busy table the migration
fails rather than hangs. It imports nothing from the application, creates no
account and recomputes nothing.

`downgrade()` exists for disposable test databases. A production rollback must
not run it: it would drop who owns every saved evaluation and the record of
every administrator read.

Revision ID: b7d41f2e9c35
Revises: a1c5e9f3b7d2
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b7d41f2e9c35"
down_revision = "a1c5e9f3b7d2"
branch_labels = None
depends_on = None


OWNED = (
    # table, timestamp column, constraint/index prefix
    ("evaluations", "created_at", "evaluations"),
    ("predictions_log", "timestamp", "predictions_log"),
)

SCOPE_CHECK = (
    "(data_scope = 'public' AND owner_user_id IS NULL) OR "
    "(data_scope = 'pilot' AND owner_user_id IS NOT NULL)"
)


def upgrade():
    # SET LOCAL: for this transaction only, and it ends with it.
    op.execute(sa.text("SET LOCAL lock_timeout = '10s'"))

    for table, _ts, prefix in OWNED:
        op.add_column(table, sa.Column("owner_user_id", sa.Uuid(as_uuid=True), nullable=True))
        op.add_column(table, sa.Column("data_scope", sa.String(16), nullable=True))

        op.execute(sa.text(
            "UPDATE %s SET data_scope = 'public' WHERE data_scope IS NULL" % table))

        op.alter_column(table, "data_scope", nullable=False,
                        server_default=sa.text("'pilot'"))
        op.create_foreign_key("fk_%s_owner_user" % prefix, table, "users",
                              ["owner_user_id"], ["id"], ondelete="RESTRICT")
        op.create_check_constraint("ck_%s_owner_scope" % prefix, table, SCOPE_CHECK)

    op.create_index("ix_evaluations_owner_recent", "evaluations",
                    ["owner_user_id", sa.text("created_at DESC"), sa.text("id DESC")])
    op.create_index("ix_evaluations_scope_recent", "evaluations",
                    ["data_scope", sa.text("created_at DESC"), sa.text("id DESC")])
    op.create_index("ix_predictions_log_owner_recent", "predictions_log",
                    ["owner_user_id", sa.text("timestamp DESC"), sa.text("id DESC")])
    op.create_index("ix_predictions_log_scope_recent", "predictions_log",
                    ["data_scope", sa.text("timestamp DESC"), sa.text("id DESC")])
    op.create_index("ix_predictions_log_scope_id", "predictions_log",
                    ["data_scope", sa.text("id DESC")])

    op.create_table(
        "project_access_events",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("actor_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("actor_username", sa.String(50), nullable=False),
        sa.Column("actor_role", sa.String(16), nullable=False),
        sa.Column("request_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("route_template", sa.String(255), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("filters", postgresql.JSONB(), nullable=True),
        sa.Column("returned_count", sa.Integer(), nullable=True),
        sa.Column("matched_count", sa.Integer(), nullable=True),
        sa.Column("affected_count", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_project_access_events"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"],
                                name="fk_project_access_events_actor", ondelete="RESTRICT"),
        sa.CheckConstraint(
            "action IN ('list', 'detail', 'export', 'delete', 'clear', 'log_read')",
            name="ck_project_access_events_action"),
        sa.CheckConstraint("outcome IN ('ok', 'not_found')",
                           name="ck_project_access_events_outcome"),
    )
    op.create_index("ix_project_access_events_recent", "project_access_events",
                    [sa.text("occurred_at DESC"), "id"])
    op.create_index("ix_project_access_events_actor", "project_access_events",
                    ["actor_user_id", sa.text("occurred_at DESC")])

    op.create_table(
        "project_access_targets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("resource_type", sa.String(16), nullable=False),
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("data_scope", sa.String(16), nullable=True),
        sa.Column("effect", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_project_access_targets"),
        sa.ForeignKeyConstraint(["event_id"], ["project_access_events.id"],
                                name="fk_project_access_targets_event", ondelete="RESTRICT"),
        sa.CheckConstraint("resource_type IN ('evaluation', 'prediction_log')",
                           name="ck_project_access_targets_resource_type"),
        sa.CheckConstraint(
            "(effect = 'not_found' AND data_scope IS NULL AND owner_user_id IS NULL) OR "
            "(effect IN ('returned', 'deleted') AND data_scope IN ('public', 'pilot'))",
            name="ck_project_access_targets_effect"),
    )
    op.create_index("ix_project_access_targets_resource", "project_access_targets",
                    ["resource_type", "resource_id", "event_id"])


def downgrade():
    op.drop_table("project_access_targets")
    op.drop_table("project_access_events")
    for name, table in (("ix_predictions_log_scope_id", "predictions_log"),
                        ("ix_predictions_log_scope_recent", "predictions_log"),
                        ("ix_predictions_log_owner_recent", "predictions_log"),
                        ("ix_evaluations_scope_recent", "evaluations"),
                        ("ix_evaluations_owner_recent", "evaluations")):
        op.drop_index(name, table_name=table)
    for table, _ts, prefix in OWNED:
        op.drop_constraint("ck_%s_owner_scope" % prefix, table, type_="check")
        op.drop_constraint("fk_%s_owner_user" % prefix, table, type_="foreignkey")
        op.drop_column(table, "data_scope")
        op.drop_column(table, "owner_user_id")
