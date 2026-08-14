"""backfill observation coordinates from what the row itself recorded

Issue #84. `latitude` and `longitude` are NULL on every row of every source.
From here the ingesters fill them; this recovers what is recoverable from rows
already written.

**Only from the row.** `openmeteo_air_quality` put the coordinates in its
metadata, so those rows carry their own answer and this reads it back. The
24,140 `openmeteo` rows carry no metadata at all -- their coordinates existed
only as a constant table in the source tree, never in the row. Reconstructing
them from `REGION_CAPITALS` would attach wherever that constant points *now* to
a reading taken then, which is the same retroactive-provenance mistake #58 made
with periods and #164 exists to undo. Those rows stay NULL, and the ingester
fills them going forward.

Row by row inside an exception block rather than one `UPDATE ... ::jsonb`:
`metadata_json` is `Text`, so nothing guarantees every row parses, and a single
malformed one would abort the whole statement -- a migration that fails on data
it was written to repair.

Idempotent: `latitude IS NULL` excludes everything a previous run touched.

Revision ID: c4e2a7b91f38
Revises: d3a71c0f5e26
Create Date: 2026-08-14

"""
from alembic import op

revision = "c4e2a7b91f38"
down_revision = "d3a71c0f5e26"
branch_labels = None
depends_on = None


BACKFILL = """
DO $$
DECLARE
    r        RECORD;
    lat      double precision;
    lon      double precision;
    filled   integer := 0;
BEGIN
    FOR r IN
        SELECT id, metadata_json
          FROM environmental_observations
         WHERE latitude IS NULL
           AND longitude IS NULL
           AND metadata_json IS NOT NULL
           AND metadata_json LIKE '%%latitude%%'
    LOOP
        BEGIN
            lat := (r.metadata_json::jsonb ->> 'latitude')::double precision;
            lon := (r.metadata_json::jsonb ->> 'longitude')::double precision;
        EXCEPTION WHEN others THEN
            -- Unparseable metadata, or a coordinate that is not a number.
            CONTINUE;
        END;

        -- The same three refusals as app/ingesters/persist.py._coordinates:
        -- both or neither, numeric, in range. A half-pair would place the row
        -- on the equator; an out-of-range value cannot be clamped onto a real
        -- point and left indistinguishable from a good one.
        IF lat IS NULL OR lon IS NULL THEN
            CONTINUE;
        END IF;
        IF lat < -90 OR lat > 90 OR lon < -180 OR lon > 180 THEN
            CONTINUE;
        END IF;

        UPDATE environmental_observations
           SET latitude = lat, longitude = lon
         WHERE id = r.id;
        filled := filled + 1;
    END LOOP;

    RAISE NOTICE 'backfilled coordinates for % observation(s)', filled;
END $$;
"""


def upgrade() -> None:
    op.execute(BACKFILL)


def downgrade() -> None:
    """Deliberately nothing.

    The reverse of this migration is not "set those coordinates back to NULL".
    By the time anyone runs it the ingesters are also writing coordinates, and
    a statement that nulls the column cannot tell a row this backfilled from a
    row the source supplied -- it would discard information nobody asked it to
    discard, to undo a step that added no column and removed nothing.

    The schema is unchanged either way, so downgrading past this point is
    already correct without doing anything.
    """
