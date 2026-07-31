"""create the four tables that only ever existed via create_all()

`batch_results`, `forecast_history`, `region_signals` and `retrain_log` are
declared in app/database.py but were created by no revision. They existed only
because `Base.metadata.create_all()` runs when the application is imported, so
`alembic upgrade head` on a clean database exited 0 and left them absent —
provisioning without starting the app produced a schema missing four tables,
with no error to explain it. See issue #51.

The DDL below was generated from the ORM models with SQLAlchemy's PostgreSQL
dialect and then frozen here, rather than imported from app.database. Alembic
revisions are history: importing the models would let a later edit silently
change what this revision does on a database that has already run it.

`IF NOT EXISTS` throughout, because the two populations have to converge:

    fresh install    nothing exists; this revision creates the canonical schema
    existing deploy  create_all() already made these tables from the same
                     models; this revision finds them and does nothing

What that does **not** do is repair a table whose shape has drifted from the
models — if a model changed after create_all() built the table, this revision
leaves the old shape in place. That divergence predates this change and is not
closed by it; converging a drifted table needs the shape-aware treatment used in
e3f8a7c15d92, and needs to know which shapes are actually out there.

Revision ID: f2c9a1d47b30
Revises: e3f8a7c15d92
Create Date: 2026-07-31 09:40:00.000000

"""
from alembic import op


revision = 'f2c9a1d47b30'
down_revision = 'e3f8a7c15d92'
branch_labels = None
depends_on = None


CREATE_SQL = r"""
CREATE TABLE IF NOT EXISTS batch_results (
    id SERIAL NOT NULL,
    batch_id VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    job_type VARCHAR(50) NOT NULL,
    total INTEGER NOT NULL,
    successful INTEGER NOT NULL,
    failed INTEGER NOT NULL,
    duration_ms FLOAT,
    status VARCHAR(50) NOT NULL,
    error_message TEXT,
    results_json TEXT,
    trigger_source VARCHAR(50) NOT NULL,
    PRIMARY KEY (id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_batch_results_batch_id ON batch_results (batch_id);
CREATE INDEX IF NOT EXISTS ix_batch_results_created_at ON batch_results (created_at);
CREATE INDEX IF NOT EXISTS ix_batch_results_id ON batch_results (id);

CREATE TABLE IF NOT EXISTS forecast_history (
    id SERIAL NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    metric VARCHAR(50) NOT NULL,
    model VARCHAR(50) NOT NULL,
    horizon INTEGER NOT NULL,
    country VARCHAR(10),
    forecast_json TEXT NOT NULL,
    confidence VARCHAR(10),
    metadata_json TEXT,
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_forecast_history_country ON forecast_history (country);
CREATE INDEX IF NOT EXISTS ix_forecast_history_created_at ON forecast_history (created_at);
CREATE INDEX IF NOT EXISTS ix_forecast_history_id ON forecast_history (id);
CREATE INDEX IF NOT EXISTS ix_forecast_history_metric ON forecast_history (metric);
CREATE INDEX IF NOT EXISTS ix_forecast_history_model ON forecast_history (model);

CREATE TABLE IF NOT EXISTS region_signals (
    id SERIAL NOT NULL,
    region_code VARCHAR(10) NOT NULL,
    source VARCHAR(32) NOT NULL,
    metric VARCHAR(64) NOT NULL,
    value FLOAT,
    unit VARCHAR(32),
    observed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    metadata_json TEXT,
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_region_signals_observed_at ON region_signals (observed_at);
CREATE INDEX IF NOT EXISTS ix_region_signals_region_code ON region_signals (region_code);
CREATE INDEX IF NOT EXISTS ix_region_signals_source ON region_signals (source);

CREATE TABLE IF NOT EXISTS retrain_log (
    id SERIAL NOT NULL,
    started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    finished_at TIMESTAMP WITHOUT TIME ZONE,
    duration_sec FLOAT,
    status VARCHAR(50) NOT NULL,
    trigger_source VARCHAR(50),
    job_name VARCHAR(100),
    model_version VARCHAR(100),
    data_version VARCHAR(100),
    metrics_json TEXT,
    error_message TEXT,
    message TEXT,
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_retrain_log_finished_at ON retrain_log (finished_at);
CREATE INDEX IF NOT EXISTS ix_retrain_log_id ON retrain_log (id);
CREATE INDEX IF NOT EXISTS ix_retrain_log_started_at ON retrain_log (started_at);
CREATE INDEX IF NOT EXISTS ix_retrain_log_status ON retrain_log (status);
CREATE INDEX IF NOT EXISTS ix_retrain_log_trigger_source ON retrain_log (trigger_source);
"""


def upgrade():
    op.execute(CREATE_SQL)


def downgrade():
    # Deliberately nothing.
    #
    # On every deployment that predates this revision these tables already
    # existed, holding data, created by create_all() rather than here. A
    # downgrade cannot tell those apart from tables this revision created, so
    # dropping them would destroy data the revision never owned -- and the
    # rollback of a schema addition is not a plausible moment to discover that.
    #
    # Removing these tables is a deliberate, separate migration, written when
    # something actually wants them gone.
    pass
