from datetime import datetime
import os
import uuid

import sqlalchemy as sa
from sqlalchemy import (
    CheckConstraint,
    BigInteger, Boolean, Column, DateTime, Float, Index, Integer, String, Text,
    JSON, create_engine, func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{os.path.join(os.path.dirname(__file__), '..', 'data', 'sora.db')}"
)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


class Evaluation(Base):
    __tablename__ = "evaluations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=True)
    budget = Column(Float)
    co2_reduction = Column(Float)
    social_impact = Column(Float)
    duration_months = Column(Integer)
    total_score = Column(Float)
    environment_score = Column(Float)
    social_score = Column(Float)
    economic_score = Column(Float)
    success_probability = Column(Float)
    recommendation = Column(Text)
    risk_level = Column(String(50))
    region = Column(String(100), default="Europe")
    lat = Column(Float, default=50.0)
    lon = Column(Float, default=10.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Ownership: see the note after PredictionLog.
    owner_user_id = Column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("users.id", name="fk_evaluations_owner_user", ondelete="RESTRICT"),
        nullable=True,
    )
    data_scope = Column(String(16), nullable=False, default="pilot",
                        server_default="pilot")

    __table_args__ = (
        CheckConstraint(
            "(data_scope = 'public' AND owner_user_id IS NULL) OR "
            "(data_scope = 'pilot' AND owner_user_id IS NOT NULL)",
            name="ck_evaluations_owner_scope",
        ),
        # An owner's history and its CSV, newest first, ties broken by id.
        Index("ix_evaluations_owner_recent",
              "owner_user_id", created_at.desc(), id.desc()),
        # The shared readers -- history for analyst and viewer, trends, the
        # forecast series -- all start with `data_scope = 'public'`.
        Index("ix_evaluations_scope_recent",
              "data_scope", created_at.desc(), id.desc()),
    )


class PredictionLog(Base):
    __tablename__ = "predictions_log"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    request_id = Column(String(64), nullable=True)
    endpoint = Column(String(50))
    model_version = Column(String(50), default="v2.0")
    budget = Column(Float)
    co2_reduction = Column(Float)
    social_impact = Column(Float)
    duration_months = Column(Integer)
    category = Column(String(100), nullable=True)
    region = Column(String(100), nullable=True)
    prediction = Column(Integer, nullable=True)
    probability = Column(Float, nullable=True)
    esg_total_score = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    latency_ms = Column(Float, nullable=True)

    owner_user_id = Column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("users.id", name="fk_predictions_log_owner_user", ondelete="RESTRICT"),
        nullable=True,
    )
    data_scope = Column(String(16), nullable=False, default="pilot",
                        server_default="pilot")

    __table_args__ = (
        CheckConstraint(
            "(data_scope = 'public' AND owner_user_id IS NULL) OR "
            "(data_scope = 'pilot' AND owner_user_id IS NOT NULL)",
            name="ck_predictions_log_owner_scope",
        ),
        Index("ix_predictions_log_owner_recent",
              "owner_user_id", timestamp.desc(), id.desc()),
        # Data health and the time-windowed aggregates.
        Index("ix_predictions_log_scope_recent",
              "data_scope", timestamp.desc(), id.desc()),
        # The KS drift window: the last N public rows by id. The filter has to
        # run before the LIMIT, and this is the index that lets it.
        Index("ix_predictions_log_scope_id", "data_scope", id.desc()),
    )


# Ownership of `evaluations` and `predictions_log`.
#
# Two scopes, and a row is in exactly one:
#
#   public   the shared corpus -- every row that existed before ownership, and
#            rows a maintenance script writes on purpose. No owner.
#   pilot    an evaluation a signed-in person saved. Always an owner, and never
#            read by anything shared: trends, data health, drift, forecasts,
#            training. Those filter on `data_scope = 'public'` -- a positive
#            match, so a scope added later is excluded until someone decides
#            otherwise, which `!= 'pilot'` or `owner_user_id IS NULL` would not
#            do.
#
# The CHECK makes the pairing a property of the table, not of the writers. The
# default is `pilot` in both the model and the database, and a pilot row needs
# an owner, so a writer that forgets to say which scope it means fails on
# insert instead of quietly publishing into the shared corpus.
#
# RESTRICT, not SET NULL or CASCADE. Accounts are blocked, never deleted
# through the API; SET NULL would erase who a row belongs to, and CASCADE would
# let deleting an account delete its projects.
OWNERSHIP_SCOPES = ("public", "pilot")


class DataRefreshLog(Base):
    __tablename__ = "data_refresh_log"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String(50), nullable=True)
    country_iso3 = Column(String(10), nullable=True)
    indicator = Column(String(100), nullable=True)
    value = Column(Float, nullable=True)
    error_message = Column(Text, nullable=True)
    fetched_at = Column(DateTime, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    job_name = Column(String(100), default="external_data_refresh")
    status = Column(String(50), nullable=False)
    countries_fetched = Column(Integer, default=0)
    total_countries = Column(Integer, default=0)
    message = Column(Text, nullable=True)
    # timezone=True to match migration 0d88c3e3c633, which declares these
    # columns `sa.DateTime(timezone=True)`. The model said naive while the
    # database has been timestamptz since that revision, and nothing reconciled
    # them -- found by the startup schema check, which is what it is for.
    #
    # The declaration alone fixes nothing about *comparisons*: a naive Python
    # datetime bound against timestamptz is interpreted in the session's
    # TimeZone, so the boundary moves. See app/api/admin_snapshot.py.
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    duration_sec = Column(Float, nullable=True)
    trigger_source = Column(String(50), nullable=True, default="manual")


class CountryIndicatorHistory(Base):
    __tablename__ = "country_indicator_history"

    id = Column(Integer, primary_key=True, index=True)
    country_iso3 = Column(String(10), nullable=False, index=True)
    country_name = Column(String(100), nullable=True, index=True)
    indicator_code = Column(String(100), nullable=False, index=True)
    indicator_name = Column(String(150), nullable=True)
    value = Column(Float, nullable=True)
    source = Column(String(50), nullable=False, default="unknown", index=True)
    as_of_date = Column(DateTime, nullable=True, index=True)
    fetched_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    refresh_job_name = Column(String(100), nullable=True)

    # Added by migration e7b3c9d15f04 and written by the #58 backfill. They
    # were absent from this model, so autogenerate read them as removed and
    # emitted drop_column for all eight -- see #88. They record why a period
    # was assigned or refused, and cannot be reconstructed if dropped.
    period_status = Column(Text, nullable=True)
    period_run_id = Column(Text, nullable=True)
    period_method = Column(Text, nullable=True)
    period_rule_version = Column(Text, nullable=True)
    period_candidates = Column(Integer, nullable=True)
    period_source_vintage = Column(Text, nullable=True)
    period_response_sha256 = Column(Text, nullable=True)
    period_resolved_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_cih_period_status", "period_status"),
        Index("ix_cih_period_run", "period_run_id"),
        # The point-in-time lookup itself (#75): greatest fetched_at <= D for a
        # country and indicator. The single-column indexes leave the planner
        # filtering and then sorting.
        Index("ix_cih_point_in_time",
              "country_iso3", "indicator_code", fetched_at.desc()),
    )


class CountryIndicatorPeriodHistory(Base):
    """Append-only record of changes to an observation's period attribution.

    The source row keeps the latest attribution for compatibility.  This table
    keeps both sides of every correction so a point-in-time reader can recover
    what period the system assigned before a later backfill changed it.
    """

    __tablename__ = "country_indicator_period_history"

    id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        sa.Identity(always=True),
        primary_key=True,
        autoincrement=True,
    )
    history_row_id = Column(Integer, nullable=False)
    country_iso3 = Column(String(10), nullable=False)
    indicator_code = Column(String(100), nullable=False)
    source = Column(String(50), nullable=False)
    value = Column(Float, nullable=True)
    fetched_at = Column(DateTime, nullable=False)
    # UTC without timezone, matching fetched_at and the cutoff accepted by
    # point_in_time.py.  Mixing timestamptz with a naive cutoff would let the
    # session TimeZone move the reconstruction boundary.
    changed_at = Column(DateTime, nullable=False)
    database_actor = Column(Text, nullable=False)

    old_as_of_date = Column(DateTime, nullable=True)
    new_as_of_date = Column(DateTime, nullable=True)
    old_period_status = Column(Text, nullable=True)
    new_period_status = Column(Text, nullable=True)
    old_period_run_id = Column(Text, nullable=True)
    new_period_run_id = Column(Text, nullable=True)
    old_period_method = Column(Text, nullable=True)
    new_period_method = Column(Text, nullable=True)
    old_period_rule_version = Column(Text, nullable=True)
    new_period_rule_version = Column(Text, nullable=True)
    old_period_candidates = Column(Integer, nullable=True)
    new_period_candidates = Column(Integer, nullable=True)
    old_period_source_vintage = Column(Text, nullable=True)
    new_period_source_vintage = Column(Text, nullable=True)
    old_period_response_sha256 = Column(Text, nullable=True)
    new_period_response_sha256 = Column(Text, nullable=True)
    old_period_resolved_at = Column(DateTime(timezone=True), nullable=True)
    new_period_resolved_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_ciph_row_changed", "history_row_id", "changed_at", "id"),
        Index(
            "ix_ciph_lookup",
            "country_iso3",
            "indicator_code",
            "fetched_at",
            "changed_at",
        ),
    )


class IngesterRun(Base):
    """One run of an ingester.

    Written only through raw SQL in app/ingesters/runner.py, which is why the
    table existed with no model at all and autogenerate proposed dropping it
    (#88). Declared here so the metadata describes the database; the runner is
    left as it is.
    """

    __tablename__ = "ingester_runs"

    # BigInteger on PostgreSQL, INTEGER on SQLite. SQLite only autoincrements a
    # column declared exactly `INTEGER PRIMARY KEY`, so a plain BigInteger PK
    # cannot be inserted through the ORM there -- which nobody had noticed,
    # because until #74 gave the table a reader every write went through raw SQL
    # in app/ingesters/runner.py against PostgreSQL. The variant leaves the
    # PostgreSQL type unchanged.
    id = Column(BigInteger().with_variant(Integer, "sqlite"),
                primary_key=True, autoincrement=True)
    source = Column(Text, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(Text, nullable=True)
    rows_written = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    execution_status = Column(Text, nullable=True)
    primary_source_status = Column(Text, nullable=True)
    data_outcome = Column(Text, nullable=True)
    records_received = Column(Integer, nullable=True)
    records_accepted = Column(Integer, nullable=True)
    records_rejected = Column(Integer, nullable=True)
    failure_reason = Column(Text, nullable=True)

    # #74. `failure_reason` is prose for a person; `reason_code` is the same
    # fact as a value, so runs can be grouped and counted without anyone
    # reading them. `required_action` is what the record exists for -- reading
    # `degraded`, nobody can tell whether to wait, retry or page someone.
    # Both are derived (app/ingesters/classification.py), never set by hand.
    reason_code = Column(Text, nullable=True)
    required_action = Column(Text, nullable=True)
    # Every input the action was derived from, not just the action.
    #
    # `source_vintage_seconds` -- age of the newest *observation* this source
    # holds, measured from event_time or period_end depending on temporal_kind.
    # Not named `age`, which reads as ingestion or cache age and is exactly the
    # confusion #121 was about.
    #
    # `max_vintage_seconds` is the tolerance that was in force *at the time of
    # the run*. A threshold can be changed afterwards, and without recording it
    # a row holding `escalate` and 590 days cannot be re-read: nobody can tell
    # whether the data was old or the tolerance moved.
    #
    # `freshness_status` says which comparison was possible at all:
    # fresh | stale | not_applicable | not_configured | unknown.
    source_vintage_seconds = Column(Float, nullable=True)
    max_vintage_seconds = Column(Float, nullable=True)
    freshness_status = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_ingester_runs_source_status",
              "source", "status", started_at.desc()),
        # The operator view sorts by action, not by time.
        Index("ix_ingester_runs_required_action",
              "required_action", started_at.desc()),
    )


class RetrainLog(Base):
    __tablename__ = "retrain_log"

    id = Column(Integer, primary_key=True, index=True)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    finished_at = Column(DateTime, nullable=True, index=True)
    duration_sec = Column(Float, nullable=True)
    status = Column(String(50), nullable=False, index=True)
    trigger_source = Column(String(50), default="scheduler", index=True)
    job_name = Column(String(100), default="model_retrain", nullable=True)

    #: One physical retrain, however many rows it writes (#199 phase 2A).
    #:
    #: A closed-loop cycle writes two rows -- `_do_retrain` records the training
    #: and the loop records the decision -- and before this they were related by
    #: nothing but a timestamp. Measured 2026-08-16 on a real cycle: row 1 said
    #: `success` with `registry_ok=False` in its own metrics, row 2 said
    #: `registry_failed` with no `model_version`, and `admin_ai` counted the
    #: cycle as one success. `retrain_models` produced two rows both saying
    #: `success`, so one run was counted twice.
    #:
    #: **NULL for every row written before this column existed.** Those runs
    #: cannot be reconstructed -- the information was never recorded -- so they
    #: are counted as one run each, which is what the old readers already did.
    #: Backfilling a guessed grouping would invent history.
    run_id = Column(String(36), nullable=True, index=True)

    #: What each stage of the run actually did (#199 phase 2B).
    #:
    #: `status` alone could not hold this. A cycle wrote `success` for the
    #: training and `rejected` for the decision as two rows, and a run whose
    #: training succeeded but whose registration did not had nowhere to say so
    #: -- measured 2026-08-16, a row read `success` while its own metrics held
    #: `registry_ok=False`.
    #:
    #: NULL on every row written before these columns existed, and on any row
    #: whose writer has not been moved yet. `status` remains the projection of
    #: these, computed in one place by `project_status`, so the twelve readers
    #: that filter on it keep working and cannot drift from the new fields.
    training_status = Column(String(30), nullable=True, index=True)
    registry_status = Column(String(30), nullable=True, index=True)
    promotion_status = Column(String(30), nullable=True, index=True)
    failure_reason = Column(Text, nullable=True)
    model_version = Column(String(100), nullable=True)
    data_version = Column(String(100), nullable=True)
    metrics_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    message = Column(Text, nullable=True)

    # The quality of the run, in columns rather than inside a JSON string.
    #
    # These values were already written -- `metrics_json` has carried roc_auc,
    # test_samples and the rest since the table existed. What did not exist was
    # any way to ask a question across runs: "has AUC moved", "on how many test
    # rows", "is 0.81 against 0.80 inside the noise". A blob answers none of
    # those, so the promotion gate compares two point estimates and cannot know
    # whether the difference is real.
    #
    # `metrics_json` is kept and still written. It holds whatever a run reports
    # that has no column yet, and dropping it would lose fields nobody has
    # promoted.
    roc_auc = Column(Float, nullable=True, index=True)
    accuracy = Column(Float, nullable=True)
    f1_score = Column(Float, nullable=True)
    #: The size the metrics above were measured on. Without it a comparison of
    #: two AUCs is a comparison of two numbers.
    test_samples = Column(Integer, nullable=True)
    train_samples = Column(Integer, nullable=True)




class BatchResultDB(Base):
    __tablename__ = "batch_results"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(String(64), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    job_type = Column(String(50), nullable=False, default="batch_evaluate")
    total = Column(Integer, nullable=False)
    successful = Column(Integer, nullable=False, default=0)
    failed = Column(Integer, nullable=False, default=0)
    duration_ms = Column(Float, nullable=True)
    status = Column(String(50), nullable=False, default="completed")
    error_message = Column(Text, nullable=True)
    results_json = Column(Text, nullable=True)
    trigger_source = Column(String(50), nullable=False, default="manual")


class ForecastHistory(Base):
    __tablename__ = "forecast_history"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    metric = Column(String(50), nullable=False, index=True)
    model = Column(String(50), nullable=False, index=True)
    horizon = Column(Integer, nullable=False)
    country = Column(String(10), nullable=True, index=True)
    forecast_json = Column(Text, nullable=False)  # JSON array of ForecastPoint objects
    confidence = Column(String(10), nullable=True)
    metadata_json = Column(Text, nullable=True)  # Additional model metadata


class ForecastModelMetrics(Base):
    """Tracks forecast model performance metrics (MAE, RMSE) over time."""
    __tablename__ = "forecast_model_metrics"

    id = Column(Integer, primary_key=True, index=True)
    trained_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    metric_name = Column(String(50), nullable=False, index=True)  # 'score', 'prob', 'co2_reduction'
    model_type = Column(String(50), nullable=False, index=True)  # 'ensemble', 'lstm', 'prophet', 'linear'
    train_samples = Column(Integer, nullable=False)  # Number of training samples
    test_samples = Column(Integer, nullable=True)  # Number of test samples (if validation performed)
    mae = Column(Float, nullable=True)  # Mean Absolute Error
    rmse = Column(Float, nullable=True)  # Root Mean Squared Error
    mape = Column(Float, nullable=True)  # Mean Absolute Percentage Error
    r2_score = Column(Float, nullable=True)  # R² coefficient of determination
    training_duration_sec = Column(Float, nullable=True)  # Training time in seconds
    trigger_source = Column(String(50), default="auto_scheduler", index=True)  # 'auto_scheduler', 'manual', 'api'
    status = Column(String(50), nullable=False, default="success", index=True)  # 'success', 'failed', 'skipped'
    error_message = Column(Text, nullable=True)  # Error details if failed
    metadata_json = Column(Text, nullable=True)  # Additional metadata (hyperparameters, etc.)


# init_db() lived here and called Base.metadata.create_all(). It is gone rather
# than merely unused: leaving it defined invites the next caller, and its one
# caller was app.main at import time -- which is how four tables came to exist
# with no migration behind them (issue #51).
#
# Alembic owns the schema now. The application checks it at startup
# (app.main.assert_schema_ready) and the test suite provisions its own scratch
# database explicitly (tests/conftest.py).


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class RegionSignal(Base):
    __tablename__ = "region_signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    region_code = Column(String(10), index=True, nullable=False)
    source = Column(String(32), index=True, nullable=False)
    metric = Column(String(64), nullable=False)
    value = Column(Float, nullable=True)
    unit = Column(String(32), nullable=True)
    observed_at = Column(DateTime(timezone=True), index=True, nullable=True)
    created_at = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    metadata_json = Column(Text, nullable=True)

class RegionESGScore(Base):
    __tablename__ = "region_esg_scores"

    # BIGINT GENERATED ALWAYS AS IDENTITY in the database; the model said
    # Integer/autoincrement, so autogenerate proposed narrowing the column and
    # dropping the identity (#88).
    id = Column(BigInteger, sa.Identity(always=True), primary_key=True)
    region_code = Column(String(10), index=True, unique=True, nullable=False)
    env_score = Column(Float, nullable=True)
    social_score = Column(Float, nullable=True)
    gov_score = Column(Float, nullable=True)
    total_score = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    sources_count = Column(Integer, nullable=True)
    signals_used = Column(Integer, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=func.now(),
                        onupdate=func.now(), nullable=False)

    # A score that is no longer backed by a complete, fresh set of
    # observations. The aggregator refuses to overwrite it -- a partial
    # recomputation would be worse -- so without these columns the row stays
    # readable as current and nothing anywhere says otherwise. That is #116
    # exactly, moved to the consumer boundary.
    #
    # `stale_since` holds when the row *first* stopped being backed, not when
    # it was last checked: "stale since Tuesday" is actionable, "stale as of
    # this run" is not.
    stale_since = Column(DateTime(timezone=True), nullable=True, index=True)
    stale_reason = Column(Text, nullable=True)

    # Declared only in a migration until #88; autogenerate read it as removed.
    __table_args__ = (
        Index("ix_region_esg_scores_id", "id", unique=True),
    )



class WebhookSubscription(Base):
    __tablename__ = "webhook_subscriptions"
    id = Column(Integer, primary_key=True)
    url = Column(String(512), nullable=False)
    event_type = Column(String(64), nullable=False, default="drift")
    secret = Column(String(128), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    id = Column(Integer, primary_key=True)
    subscription_id = Column(Integer, index=True)
    event_type = Column(String(64))
    status_code = Column(Integer)
    ok = Column(Boolean, default=False)
    error = Column(String(512))
    created_at = Column(DateTime, default=datetime.utcnow)


class HealthPing(Base):
    __tablename__ = "health_pings"
    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, default=datetime.utcnow, index=True)
    component = Column(String(32), index=True)
    ok = Column(Boolean, default=True)


class EnvironmentalObservation(Base):
    """Environmental observation from external data sources.

    Stores real-time and historical environmental measurements with full
    provenance tracking. Designed for time-series analysis with point-in-time
    correctness.

    Requirements from ROADMAP_ENV_CRISIS_2026.md Phase 1.1:
    - Unique constraint on (source, source_record_id) prevents duplicates
    - UTC-aware timestamps for event_time, published_at, ingested_at
    - Separate event time (when it happened) from publication and ingestion
    - Quality score and validity flag for data quality pipeline
    - Indexed for efficient time-series queries

    Example indicators:
    - pm25, pm10, no2, o3, so2, co (air quality)
    - temperature, humidity, pressure, wind_speed (weather)
    - precipitation, heat_index, aqi (derived metrics)
    """
    __tablename__ = "environmental_observations"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Geographic location
    region_id = Column(String(32), nullable=False, index=True)
    country_code = Column(String(3), nullable=True, index=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    # Measurement
    indicator = Column(String(64), nullable=False, index=True)
    value = Column(Float, nullable=True)
    unit = Column(String(32), nullable=True)

    # Provenance
    source = Column(String(64), nullable=False, index=True)
    source_record_id = Column(String(128), nullable=True)
    # 128, matching source_record_id. A content-hash revision is
    # `rev:v1:` plus a 64-character sha256 -- 71 characters, which did not fit
    # in 64 and made both literal ingesters unable to write anything at all.
    # PostgreSQL enforces the length; SQLite ignores it, so every test passed
    # and only production refused (#121).
    source_revision = Column(String(128), nullable=True)

    # Temporal tracking (all UTC-aware)
    #
    # `event_time` is nullable because not every source has one. It used to be
    # NOT NULL, and persistence therefore filled it with `datetime.now()` for
    # the two sources that emit literals -- a dict of 85 constants and an
    # offline 2024 snapshot were both recorded as measured today (#121). A
    # column that cannot say "there is no observation time" forces every writer
    # to invent one.
    #
    # What kind of time a row carries, and the rules per kind, are stated once
    # in app/ingesters/temporal.py; the CHECK constraints below mirror that
    # table so a direct write cannot route around the validator.
    event_time = Column(DateTime(timezone=True), nullable=True, index=True)
    period_start = Column(DateTime(timezone=True), nullable=True)
    period_end = Column(DateTime(timezone=True), nullable=True)
    # No server_default. One would silently make every row that omits the kind
    # an `observed` -- a claim that a value was measured, asserted by a column
    # default rather than by anyone who looked. That is the defect this whole
    # change removes, reintroduced on the field meant to fix it. The migration
    # does not need it either: it adds the column nullable, backfills from a
    # frozen mapping, and only then sets NOT NULL.
    temporal_kind = Column(String(32), nullable=False, index=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    ingested_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)

    # Data quality
    quality_score = Column(Float, nullable=True)  # 0-100 scale
    is_valid = Column(Boolean, nullable=False, server_default=sa.text('true'), index=True)

    # Metadata (JSON for flexible extension)
    metadata_json = Column(Text, nullable=True)

    # Audit trail
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    # These were deliberately left to the migration and noted here as a
    # comment. The consequence was not noted: autogenerate diffs metadata
    # against the live schema, so an index the model does not declare reads as
    # removed, and the next generated migration dropped all three -- including
    # the partial unique index that is the only thing preventing duplicate
    # ingestion. See #88. Declaring them here changes no DDL; it makes the
    # metadata describe what the database already has.
    # The rules from app/ingesters/temporal.py, stated a second time on
    # purpose. The validator gives a clear error before an INSERT; these stop a
    # migration, a repair script or a psql session writing past it -- and those
    # are exactly the paths that produced the rows #121 is about.
    #
    # String + named CHECK rather than a PostgreSQL Enum: the project does not
    # use Enum systematically, this checks identically on SQLite and
    # PostgreSQL, and adding a value later is a constraint swap rather than a
    # type migration.
    #
    # Named because a migration has to create and drop them by hand; a
    # generated name differs between backends and cannot be referred to.
    __table_args__ = (
        CheckConstraint(
            "temporal_kind IN ('observed', 'period', 'not_applicable', "
            "'legacy_ingestion_time')",
            name="ck_environmental_observations_temporal_kind_known",
        ),
        CheckConstraint(
            # observed and legacy both carry a timestamp and no period. They
            # differ in what that timestamp means, not in its shape, so the
            # shape is checked once.
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
            name="ck_environmental_observations_temporal_kind_state",
        ),
        Index("ix_environmental_observations_source_record_unique",
              "source", "source_record_id",
              unique=True,
              postgresql_where=sa.text("source_record_id IS NOT NULL")),
        Index("ix_environmental_observations_region_indicator_time",
              "region_id", "indicator", "event_time"),
        Index("ix_environmental_observations_source_ingested",
              "source", "ingested_at"),
    )


class EnvironmentalJobLog(Base):
    """Execution log for environmental data scheduler jobs.

    Tracks job execution history with performance metrics and error tracking.
    Used for monitoring scheduler health, debugging failures, and calculating
    data source quality scores.

    Job types:
    - openaq_ingestion: Hourly OpenAQ air quality data fetch
    - openmeteo_ingestion: Hourly Open-Meteo weather data fetch
    - source_health_check: 15-minute source availability monitoring
    - data_quality_aggregation: Hourly quality metrics aggregation
    """
    __tablename__ = "environmental_job_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_name = Column(String(100), nullable=False, index=True)
    executed_at = Column(DateTime(timezone=True), nullable=False, index=True)
    status = Column(String(20), nullable=False, index=True)  # success, failed, skipped
    duration_sec = Column(Float, nullable=True)
    records_processed = Column(Integer, nullable=True, server_default="0")
    records_rejected = Column(Integer, nullable=True, server_default="0")
    error_message = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)  # Job-specific metadata


#: Roles an account may hold. `specialist` is the pilot participant: it is not
#: `analyst`, because `require_analyst_or_admin` admits analysts to operational
#: endpoints a participant has no business reaching.
USER_ROLES = ("admin", "analyst", "viewer", "specialist")


class User(Base):
    """An account. The only place accounts live.

    Accounts used to be a module-level dict in app/auth.py rebuilt at import
    from SORA_DEFAULT_*_PASSWORD, so anything created at runtime was gone on
    the next restart, and blocking someone meant changing an environment
    variable and redeploying.

    `token_version` is the generation of every session the account holds. A
    token carries the version it was issued at, and is accepted only while the
    row still has that version, so resetting a password, changing a role,
    blocking -- and unblocking -- revokes every outstanding token in every
    worker at the moment the transaction commits. It is read on every request,
    not cached: a cache is exactly the window revocation is meant to close.

    `password_changed_at` is informational. It deliberately plays no part in
    revocation -- comparing a token's `iat` against it brings clock skew and
    second-resolution ties into a security decision for no gain.
    """
    __tablename__ = "users"

    id = Column(sa.Uuid(as_uuid=True), primary_key=True)
    username = Column(String(50), nullable=False)
    password_hash = Column(Text, nullable=False)
    role = Column(String(16), nullable=False)
    is_blocked = Column(Boolean, nullable=False, server_default=sa.false())
    token_version = Column(BigInteger, nullable=False, server_default=sa.text("1"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    password_changed_at = Column(DateTime(timezone=True), nullable=False,
                                 server_default=func.now())
    created_by_user_id = Column(sa.Uuid(as_uuid=True),
                                sa.ForeignKey("users.id", name="fk_users_created_by",
                                              ondelete="RESTRICT"),
                                nullable=True)

    __table_args__ = (
        sa.UniqueConstraint("username", name="uq_users_username"),
        CheckConstraint(
            "username = lower(trim(username)) AND length(username) BETWEEN 3 AND 50",
            name="ck_users_username",
        ),
        CheckConstraint(
            "role IN ('admin', 'analyst', 'viewer', 'specialist')",
            name="ck_users_role",
        ),
        CheckConstraint("token_version > 0", name="ck_users_token_version"),
        CheckConstraint("password_hash LIKE '$argon2id$%'",
                        name="ck_users_password_argon2id"),
    )


class ProjectAccessEvent(Base):
    """One administrator request that read or removed project rows.

    Durable, unlike app/audit.py, whose deque lives in one worker and is gone
    on restart. An administrator can read every specialist's projects; this is
    what makes that reading traceable after the fact.

    Written before the data leaves the server: the route records the event and
    its targets and commits, and only then returns. If the commit fails the
    request fails with it (app/project_audit.py). For a deletion the event and
    the DELETE are one transaction.

    No credentials, request body, project names or computed values are stored
    -- the event names who did what to which ids, nothing about their content.
    There is no API to edit or delete these rows.
    """
    __tablename__ = "project_access_events"

    id = Column(sa.Uuid(as_uuid=True), primary_key=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    actor_user_id = Column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("users.id", name="fk_project_access_events_actor", ondelete="RESTRICT"),
        nullable=False,
    )
    # A snapshot of who they were at the time. A later rename or role change
    # does not rewrite what the event says.
    actor_username = Column(String(50), nullable=False)
    actor_role = Column(String(16), nullable=False)
    # Generated here, per request, by the server -- never taken from a header
    # a client could set.
    request_id = Column(sa.Uuid(as_uuid=True), nullable=False, default=uuid.uuid4)
    action = Column(String(16), nullable=False)
    method = Column(String(8), nullable=False)
    route_template = Column(String(255), nullable=False)
    status_code = Column(Integer, nullable=False)
    outcome = Column(String(16), nullable=False)
    filters = Column(sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True)
    returned_count = Column(Integer, nullable=True)
    matched_count = Column(Integer, nullable=True)
    affected_count = Column(Integer, nullable=True)
    reason = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "action IN ('list', 'detail', 'export', 'delete', 'clear', 'log_read')",
            name="ck_project_access_events_action",
        ),
        CheckConstraint("outcome IN ('ok', 'not_found')",
                        name="ck_project_access_events_outcome"),
        Index("ix_project_access_events_recent", occurred_at.desc(), "id"),
        Index("ix_project_access_events_actor", "actor_user_id", occurred_at.desc()),
    )


class ProjectAccessTarget(Base):
    """Which rows one ProjectAccessEvent touched, and what happened to each.

    One row per id, not a truncated list in a text column: a CSV export of
    four hundred evaluations names all four hundred.

    `resource_id` deliberately has no foreign key. The record of a deletion has
    to outlive the row it deleted. `owner_user_id` and `data_scope` are a
    snapshot taken at the time, for the same reason. A `not_found` target is
    an id that was asked for and did not exist, so it has neither.
    """
    __tablename__ = "project_access_targets"

    id = Column(BigInteger().with_variant(Integer, "sqlite"),
                primary_key=True, autoincrement=True)
    event_id = Column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("project_access_events.id", name="fk_project_access_targets_event",
                      ondelete="RESTRICT"),
        nullable=False,
    )
    resource_type = Column(String(16), nullable=False)
    resource_id = Column(BigInteger, nullable=False)
    owner_user_id = Column(sa.Uuid(as_uuid=True), nullable=True)
    data_scope = Column(String(16), nullable=True)
    effect = Column(String(16), nullable=False)

    __table_args__ = (
        CheckConstraint("resource_type IN ('evaluation', 'prediction_log')",
                        name="ck_project_access_targets_resource_type"),
        CheckConstraint(
            "(effect = 'not_found' AND data_scope IS NULL AND owner_user_id IS NULL) OR "
            "(effect IN ('returned', 'deleted') AND data_scope IN ('public', 'pilot'))",
            name="ck_project_access_targets_effect",
        ),
        Index("ix_project_access_targets_resource", "resource_type", "resource_id", "event_id"),
    )


def count_physical_runs(db, status=None, since=None) -> int:
    """How many retrains happened, not how many rows were written (#199 phase 2A).

    A closed-loop cycle writes two rows and `retrain_models` writes two rows,
    both saying `success` -- so `db.query(RetrainLog).count()` reported one run
    as two, and `admin_ai` reported a rejected cycle as a success.

    Rows carrying a `run_id` are grouped by it. Rows without one predate the
    column and are counted individually, because the grouping they belonged to
    was never recorded and guessing it would put a reconstruction where a fact
    is expected. That is also exactly what the old readers did, so historical
    figures do not move.

    `status` filters before grouping. A run whose rows disagree -- `success` for
    the training and `rejected` for the decision, which is the normal shape --
    therefore counts once under each, and that is the honest answer: the run did
    both of those things.

    `since` filters the same way, on `started_at`, so "runs in the last N hours"
    is one run per `run_id` too. It is a parameter here rather than a second
    grouping at the call site: this function is the only definition of what
    "how many runs" means, and two implementations of that question is how the
    project ended up with two promotion gates.
    """
    from sqlalchemy import func

    query = db.query(RetrainLog)
    if status is not None:
        query = query.filter(RetrainLog.status == status)
    if since is not None:
        query = query.filter(RetrainLog.started_at >= since)

    grouped = (
        query.filter(RetrainLog.run_id.isnot(None))
        .with_entities(RetrainLog.run_id)
        .distinct()
        .count()
    )
    ungrouped = query.filter(RetrainLog.run_id.is_(None)).count()
    return int(grouped) + int(ungrouped)


#: The only definition of what `status` means in terms of the split fields
#: (#199 phase 2B).
#:
#: One definition on purpose. Two implementations of "what does this mean now"
#: is exactly how `closed_loop_retrain` and `app/api/infra.py` ended up with two
#: different promotion gates, one of which had no registry check at all.
#:
#: The mapping reproduces the values already written, so no reader changes
#: behaviour: `promoted`/`rejected` when the gate has ruled, otherwise the
#: training's own outcome, otherwise `running`.
#:
#: `registry_status` deliberately does not move `status` here. Making a failed
#: registration visible in the top-level status is a contract change of its
#: own, kept out of this split -- doing both at once would make it impossible
#: to tell which change altered a figure. #189 (closed 2026-09-04) and #198
#: (a superseded pull request) are where the underlying registration bug was
#: found and first attempted, not where this specific change is tracked;
#: nothing currently is.
def project_status(training_status=None, registry_status=None,
                   promotion_status=None) -> str:
    if promotion_status in ("promoted", "rejected"):
        return promotion_status
    if training_status in ("failed", "skipped", "success"):
        return training_status
    return "running"
