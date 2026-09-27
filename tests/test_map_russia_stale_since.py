"""
Test that app/routes/map_russia.py emits stale_since in all read paths.

The backend writes stale_since and stale_reason to region_esg_scores when a
region's inputs stop being fresh (app/services/esg_aggregator.py _mark_stale),
but the view regional_esg_snapshot did not expose them until the migration
e8f9a1b2c3d4. This test verifies that every read path in map_russia.py now
emits stale_since.
"""
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal, RegionESGScore

client = TestClient(app)


def test_sqlalchemy_fallback_emits_stale_since_for_stale_region():
    """
    The SQLAlchemy fallback path on SQLite emits stale_since for a stale
    RegionESGScore and null for a fresh one.
    """
    db = SessionLocal()
    try:
        # Clear any existing rows
        db.query(RegionESGScore).delete()
        db.commit()

        # Create a stale region
        stale_date = datetime.now(timezone.utc) - timedelta(days=7)
        # On SQLite, IDENTITY doesn't work, so we provide id explicitly
        stale_region = RegionESGScore(
            id=1,
            region_code="RU-TEST-STALE",
            env_score=70.0,
            social_score=75.0,
            gov_score=80.0,
            total_score=75.0,
            confidence=0.67,
            sources_count=2,
            signals_used=10,
            stale_since=stale_date,
            stale_reason="test stale reason",
            updated_at=datetime.now(timezone.utc) - timedelta(days=8),
        )

        # Create a fresh region
        fresh_region = RegionESGScore(
            id=2,
            region_code="RU-TEST-FRESH",
            env_score=70.0,
            social_score=75.0,
            gov_score=80.0,
            total_score=75.0,
            confidence=0.67,
            sources_count=2,
            signals_used=10,
            stale_since=None,
            stale_reason=None,
            updated_at=datetime.now(timezone.utc),
        )

        db.add(stale_region)
        db.add(fresh_region)
        db.commit()

        # Call the endpoint - it will use the SQLAlchemy fallback on SQLite
        response = client.get("/api/v1/map/russia")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        data = response.json()
        assert "regions" in data, f"Expected 'regions' in response: {data}"
        regions = data["regions"]

        # Find our test regions
        stale = next((r for r in regions if r["code"] == "RU-TEST-STALE"), None)
        fresh = next((r for r in regions if r["code"] == "RU-TEST-FRESH"), None)

        assert stale is not None, f"Expected to find RU-TEST-STALE in {[r['code'] for r in regions]}"
        assert fresh is not None, f"Expected to find RU-TEST-FRESH in {[r['code'] for r in regions]}"

        # Verify stale_since is emitted correctly
        assert "stale_since" in stale, f"Expected 'stale_since' in stale region: {stale.keys()}"
        assert stale["stale_since"] is not None, "Expected stale_since to be set for stale region"
        # Verify it's a valid ISO timestamp
        parsed = datetime.fromisoformat(stale["stale_since"].replace("Z", "+00:00"))
        assert parsed.date() == stale_date.date(), f"Expected {stale_date.date()}, got {parsed.date()}"

        assert "stale_since" in fresh, f"Expected 'stale_since' in fresh region: {fresh.keys()}"
        assert fresh["stale_since"] is None, f"Expected stale_since to be null for fresh region, got {fresh['stale_since']}"

    finally:
        db.query(RegionESGScore).filter(
            RegionESGScore.region_code.in_(["RU-TEST-STALE", "RU-TEST-FRESH"])
        ).delete()
        db.commit()
        db.close()
