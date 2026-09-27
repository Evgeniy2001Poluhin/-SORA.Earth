"""
Test that the regional_esg_snapshot view exposes stale_since and stale_reason.

The migration e8f9a1b2c3d4 appends stale_since and stale_reason to the view.
This test verifies that:
1. After upgrade head, the view exposes stale_since equal to the table's
2. The 8 original columns are unchanged in name, order, and expression
"""
from datetime import datetime, timezone, timedelta
from sqlalchemy import text
from tests.postgres_scratch import scratch_db, requires_postgres


@requires_postgres
def test_view_exposes_stale_columns_and_preserves_original_eight(scratch_db):
    """
    After upgrade head, the view exposes stale_since matching the table's,
    and the 8 original columns are unchanged.
    """
    engine, url = scratch_db

    with engine.connect() as conn:
        # Insert a stale row into region_esg_scores
        stale_since = datetime.now(timezone.utc) - timedelta(days=7)
        conn.execute(
            text("""
                INSERT INTO region_esg_scores (
                    region_code, env_score, social_score, gov_score, total_score,
                    confidence, sources_count, signals_used, stale_since, stale_reason,
                    updated_at
                ) VALUES (
                    :code, :e, :s, :g, :total,
                    :conf, :sc, :sig, :stale, :reason,
                    :updated
                )
            """),
            {
                "code": "RU-TEST",
                "e": 70.0,
                "s": 75.0,
                "g": 80.0,
                "total": 75.0,
                "conf": 0.67,
                "sc": 2,
                "sig": 10,
                "stale": stale_since,
                "reason": "test stale reason",
                "updated": datetime.now(timezone.utc) - timedelta(days=8),
            },
        )
        conn.commit()

        # Query the view
        row = conn.execute(
            text("SELECT * FROM regional_esg_snapshot WHERE region_code = :code"),
            {"code": "RU-TEST"},
        ).fetchone()

        assert row is not None, "Expected to find RU-TEST in the view"

        # The view has 10 columns: the original 8 plus stale_since and stale_reason
        # Original 8: region_code, e_score, s_score, g_score, score, confidence,
        #             sources_used, computed_at
        # New 2: stale_since, stale_reason
        assert len(row) == 10, f"Expected 10 columns, got {len(row)}: {row.keys()}"

        # Verify the original 8 columns are present and unchanged
        assert row[0] == "RU-TEST", f"Column 0 (region_code) expected 'RU-TEST', got {row[0]}"
        assert row[1] == 70.0, f"Column 1 (e_score) expected 70.0, got {row[1]}"
        assert row[2] == 75.0, f"Column 2 (s_score) expected 75.0, got {row[2]}"
        assert row[3] == 80.0, f"Column 3 (g_score) expected 80.0, got {row[3]}"
        assert row[4] == 75.0, f"Column 4 (score) expected 75.0, got {row[4]}"
        assert row[5] == 0.67, f"Column 5 (confidence) expected 0.67, got {row[5]}"
        assert row[6] == [], f"Column 6 (sources_used) expected [], got {row[6]}"
        # Column 7 is computed_at (updated_at), which is a timestamp - just verify it's not None
        assert row[7] is not None, f"Column 7 (computed_at) expected a timestamp, got None"

        # Verify the new columns are at the end
        # Column 8 is stale_since
        assert row[8] is not None, f"Column 8 (stale_since) expected a timestamp, got None"
        # Verify it matches what we inserted (allowing for microsecond differences)
        assert abs((row[8] - stale_since).total_seconds()) < 1, (
            f"Column 8 (stale_since) expected {stale_since}, got {row[8]}"
        )

        # Column 9 is stale_reason
        assert row[9] == "test stale reason", (
            f"Column 9 (stale_reason) expected 'test stale reason', got {row[9]}"
        )

        # Verify column names explicitly
        keys = list(row.keys())
        expected_keys = [
            "region_code", "e_score", "s_score", "g_score", "score",
            "confidence", "sources_used", "computed_at", "stale_since", "stale_reason"
        ]
        assert keys == expected_keys, (
            f"Expected column names {expected_keys}, got {keys}"
        )

        # Insert a fresh row (stale_since = NULL)
        conn.execute(
            text("""
                INSERT INTO region_esg_scores (
                    region_code, env_score, social_score, gov_score, total_score,
                    confidence, sources_count, signals_used, stale_since, stale_reason,
                    updated_at
                ) VALUES (
                    :code, :e, :s, :g, :total,
                    :conf, :sc, :sig, NULL, NULL,
                    :updated
                )
            """),
            {
                "code": "RU-FRESH",
                "e": 70.0,
                "s": 75.0,
                "g": 80.0,
                "total": 75.0,
                "conf": 0.67,
                "sc": 2,
                "sig": 10,
                "updated": datetime.now(timezone.utc),
            },
        )
        conn.commit()

        fresh = conn.execute(
            text("SELECT stale_since, stale_reason FROM regional_esg_snapshot WHERE region_code = :code"),
            {"code": "RU-FRESH"},
        ).fetchone()

        assert fresh is not None, "Expected to find RU-FRESH in the view"
        assert fresh[0] is None, f"Expected stale_since to be NULL for fresh region, got {fresh[0]}"
        assert fresh[1] is None, f"Expected stale_reason to be NULL for fresh region, got {fresh[1]}"
