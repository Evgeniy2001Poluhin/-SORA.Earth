"""encode temporal provenance on environmental_observations

Empty on purpose, for now. It exists so the migration tests have a real target
revision to upgrade to: without one they would fail because the file is
missing, which says nothing about the contract. With it, they fail because an
empty migration does not implement the contract -- which is the failure worth
having.

Parent is d3f0a71c9b48, the actual head. `f7a8b9c0d1e2` created the table and
looks like the semantic parent, but chaining there would fork the graph: those
two heads were already merged by 013bbc52a33f, and production sits on
d3f0a71c9b48 with a single row in alembic_version.

What this will do, once written (#121):

  1. add temporal_kind, period_start, period_end as nullable
  2. drop NOT NULL from event_time
  3. preflight, before any DDL: refuse when a source is unknown or NULL,
     naming each one with its row count
  4. classify the known sources from a frozen mapping held in this file --
     not imported from app/ingesters/source_register.py, so that editing the
     registry later cannot change what an already-released migration did
  5. assert nothing is left unclassified
  6. make temporal_kind NOT NULL
  7. add the two named CHECK constraints that mirror the model
  8. assert the row count and the pre-existing columns are unchanged

The frozen mapping, measured against production on 2026-08-12:

    openaq                 observed                  0 rows
    openmeteo              observed              61 090
    openmeteo_air_quality  observed              21 918
    rosstat                legacy_ingestion_time 10 200
    sber_veb_baseline      legacy_ingestion_time  2 040

`openaq` is registered and its semantics are known, so it is classified even
though production holds none of its rows. `world_bank` is deliberately absent:
it appears in the code for a different table, and a row of it turning up here
should stop the migration rather than receive a plausible guess.

The counts are a measurement, not an invariant. The migration compares its own
before and after counts; it must work for a table of any size holding those
same sources.

Downgrade will be fail-closed. There is no honest event_time to invent for a
`period` or `not_applicable` row, and inventing one would restore exactly the
falsehood this migration removes.

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


def upgrade() -> None:
    """Not implemented yet; the tests for it are written first."""


def downgrade() -> None:
    """Not implemented yet; the tests for it are written first."""
