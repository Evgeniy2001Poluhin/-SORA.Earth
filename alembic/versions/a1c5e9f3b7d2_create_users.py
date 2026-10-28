"""create users

Accounts move out of the module-level dict in app/auth.py into PostgreSQL, so
an account created at runtime survives a restart and blocking or resetting one
revokes its sessions in every worker (see app/database.py:User).

Schema only. This migration does not import app.auth, read a password or create
an account: the `migrate` service runs with DATABASE_URL and nothing else, and
the initial accounts are created by scripts/bootstrap_users.py, a separate step
of the deployment that is given the bootstrap passwords and nothing else needs.

`downgrade()` exists for disposable test databases. A production rollback must
not drop this table: it holds accounts that exist nowhere else.

Revision ID: a1c5e9f3b7d2
Revises: f4b7c2d91e06
"""

from alembic import op
import sqlalchemy as sa


revision = "a1c5e9f3b7d2"
down_revision = "f4b7c2d91e06"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("is_blocked", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("token_version", sa.BigInteger(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"],
                                name="fk_users_created_by", ondelete="RESTRICT"),
        sa.CheckConstraint(
            "username = lower(trim(username)) AND length(username) BETWEEN 3 AND 50",
            name="ck_users_username",
        ),
        sa.CheckConstraint(
            "role IN ('admin', 'analyst', 'viewer', 'specialist')",
            name="ck_users_role",
        ),
        sa.CheckConstraint("token_version > 0", name="ck_users_token_version"),
        sa.CheckConstraint("password_hash LIKE '$argon2id$%'",
                           name="ck_users_password_argon2id"),
    )


def downgrade():
    op.drop_table("users")
